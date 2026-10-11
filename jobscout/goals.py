"""Goals — one coherent, standalone search target layered over the base profile.

A goal is NOT a bundle of conditions. "Austria, senior, Düsseldorf" is not a
goal: it is three different goals stitched together, and two of them are in
different countries. Each goal here expresses ONE dimension and must be
satisfiable on its own — the test is whether a real posting could satisfy it
without contradicting itself.

The base profile (PHP/Laravel/TYPO3 + DACH + remote) always applies. A goal
adds a single dimension on top of it, never replaces or filters the base.

One goal is active at a time. Many goals may be defined; the active one is what
`list`/`triage`/`digest` default to, so the number you see is a number you can
act on. Everything is optional: with no `goals:` block, or with no active goal,
jobscout behaves exactly as before.

Matching is deterministic and reuses the rule engine's existing text matching,
so a goal never contradicts what `scan` already decided about a posting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Goal:
    """One standalone search target.

    Exactly one of the dimension groups is expected to be populated, but the
    code does not enforce that: it is the user's config, and a goal that
    contradicts itself simply matches nothing, which is the honest outcome.
    """
    name: str
    kind: str = "terms"                     # terms | location | seniority | salary
    label: str = ""
    terms: list = field(default_factory=list)       # matched against title+company
    locations: list = field(default_factory=list)   # matched against location only
    salary_min: float | None = None                 # EUR-equivalent annual floor
    min_score: float | None = None                  # extra bar on top of the base
    # where `terms` is matched. "title" is the default and the right choice for
    # most goals: a body mention like "lead a team" is not a real signal for a
    # seniority goal. "all" adds company+description, for goals about topics
    # rather than rank.
    scope: str = "title"
    enabled: bool = True

    def describe(self) -> str:
        if self.label:
            return self.label
        if self.kind == "location":
            return "in " + ", ".join(self.locations)
        if self.kind == "salary":
            return f"at least {self.salary_min:,.0f} EUR/year"
        if self.kind == "seniority":
            return "senior roles"
        return ", ".join(self.terms)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "label": self.label,
            "terms": list(self.terms), "locations": list(self.locations),
            "salary_min": self.salary_min, "min_score": self.min_score,
            "scope": self.scope,
            "enabled": self.enabled,
        }


def load_goals(raw: dict) -> dict:
    """Parse the `goals:` block. Returns {name: Goal}, skipping malformed entries."""
    out: dict[str, Goal] = {}
    block = (raw or {}).get("goals") or {}
    if not isinstance(block, dict):
        return out
    for name, spec in block.items():
        if isinstance(spec, str):                      # "goals: {austria: Austria}"
            spec = {"label": spec}
        if not isinstance(spec, dict):
            continue
        try:
            g = Goal(
                name=str(name),
                kind=str(spec.get("kind") or "terms"),
                label=str(spec.get("label") or ""),
                terms=[str(x) for x in (spec.get("terms") or [])],
                locations=[str(x) for x in (spec.get("locations") or [])],
                salary_min=(float(spec["salary_min"])
                            if spec.get("salary_min") is not None else None),
                min_score=(float(spec["min_score"])
                           if spec.get("min_score") is not None else None),
                scope=str(spec.get("scope") or "title"),
                enabled=bool(spec.get("enabled", True)),
            )
        except (TypeError, ValueError):
            continue
        out[g.name] = g
    return out


def active_goal(goals: dict, name: str | None) -> Goal | None:
    """The one active goal, or None. Never raises: goals are always optional."""
    if not goals:
        return None
    if name:
        return goals.get(name)
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _hits(text: str, terms: list) -> list:
    low = _norm(text)
    return [t for t in terms if _norm(t) and _norm(t) in low]


def match(p, g: Goal | None) -> bool:
    """Does posting `p` satisfy goal `g`? Pure, no DB, no side effects.

    `p` is a sqlite3.Row or anything with title/company/location/salary_* /
    score attributes. The base profile has already been applied upstream: this
    only ever narrows, never widens, and it never resurrects a filtered row.
    """
    if not g or not g.enabled:
        return False

    if g.kind == "location":
        loc = _norm(p["location"])
        if not any(_norm(t) in loc for t in g.locations):
            return False
    elif g.kind == "salary":
        if g.salary_min is None:
            return False
        # salary_annual_max is the comparable figure; postings with unknown pay
        # never satisfy a salary goal, because "no number" is not "enough"
        val = p["salary_annual_max"]
        if val is None:
            return False
        if float(val) < float(g.salary_min):
            return False
    else:
        # terms / seniority. `scope` decides how much text counts: title-only
        # (default) keeps a goal from firing on incidental body mentions.
        if g.scope == "all":
            hay = " ".join((p["title"] or "", p["company"] or "", p["description"] or ""))
        else:
            hay = " ".join((p["title"] or "", p["company"] or ""))
        if not _hits(hay, g.terms):
            return False

    if g.min_score is not None and float(p["score"] or 0) < g.min_score:
        return False
    return True


def matches_ids(conn, g: Goal, rows=None) -> set:
    """IDs of stored postings satisfying `g`, among `rows` (or all rows)."""
    if rows is None:
        rows = conn.execute(
            "SELECT id,title,company,location,description,score,"
            "salary_annual_max FROM postings").fetchall()
    return {r["id"] for r in rows if match(r, g)}


def counts_by_goal(conn, goals: dict, rows=None) -> dict:
    """Live match count per goal, so `jobscout goals` can show what each yields."""
    if rows is None:
        rows = conn.execute(
            "SELECT id,title,company,location,description,score,"
            "salary_annual_max FROM postings WHERE filtered=0 AND dup_of IS NULL"
        ).fetchall()
    return {name: sum(1 for r in rows if match(r, g))
            for name, g in goals.items()}
