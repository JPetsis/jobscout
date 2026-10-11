"""Headless service layer — the single contract shared by the CLI and the GUI.

Why this exists: the GUI must not call `cmd_*` functions (they take an
argparse.Namespace, print to stdout, and load the config implicitly), and it
must not re-implement any rule. Everything here returns plain data and defers
to the existing core, so `score.py` / `geo.py` / `salary.py` / `sources/*` stay
the only place the logic lives.

Nothing in this module prints. Config writes go through `cfgwrite`, which
edits the file line-wise so the 137 comment lines documenting it survive —
see that module for why a yaml round-trip is not an option.
"""
from __future__ import annotations

import json
import pathlib

from . import db as dbm
from . import salary as salmod

VALID_STATUSES = ("new", "saved", "applied", "dismissed")


def posting_dict(row) -> dict:
    """One stored posting as a JSON-safe dict.

    `reasons` is stored as JSON *text* in the DB; the UI needs the parsed list
    to render its "why" chips. A malformed value yields `[]` rather than raising,
    because a hand-edited or partially-migrated DB must not break the whole view.
    """
    out = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else dict(row)
    raw = out.get("reasons") or ""
    try:
        parsed = json.loads(raw) if raw else []
        out["reasons"] = parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        out["reasons"] = []
    try:
        out["salary"] = salmod.fmt_row(row) or ""
    except Exception:                                        # noqa: BLE001
        out["salary"] = ""
    out["score"] = float(out.get("score") or 0.0)
    return out


def list_matches(conn, *, min_score=None, status=None, source=None,
                 limit=None) -> list[dict]:
    """Stored postings that survived the rule engine, best first.

    Excludes filtered rows and duplicates by default so the count in the window
    matches the count in the digest.

    Holds dbm.LOCK: this runs on pywebview worker threads, and a read
    interleaving with a concurrent `mark_posting` write is a real race.
    """
    with dbm.LOCK:
        rows = dbm.list_postings(conn, min_score=min_score, status=status,
                                 source=source, limit=limit or 100000)
        out = [posting_dict(r) for r in rows
               if not r["filtered"] and r["dup_of"] is None]
    out.sort(key=lambda r: r["score"], reverse=True)
    if limit:
        out = out[:limit]
    return out


def get_posting(conn, pid: int) -> dict | None:
    with dbm.LOCK:
        row = dbm.get_posting(conn, pid)
    return posting_dict(row) if row is not None else None


def mark_posting(conn, pid: int, status: str) -> dict:
    """Set a posting's triage status. Rejects anything not in VALID_STATUSES.

    A GUI can send an arbitrary string, so validate rather than trusting it.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES!r}, got {status!r}")
    with dbm.LOCK:
        dbm.set_status(conn, int(pid), status)
    return {"id": int(pid), "status": status}


def source_counts(conn) -> list[dict]:
    """Per-source row counts, for the source filter dropdown."""
    with dbm.LOCK:
        counts = dbm.counts(conn) or {}
    return [{"source": s, "count": n} for s, n in counts.items()]


def stats(conn, prefs: dict) -> dict:
    """Header numbers plus the score histogram used to calibrate thresholds."""
    min_score = float(prefs.get("min_score", 6))
    strong_th = float(prefs.get("strong_threshold", 10))
    with dbm.LOCK:
        matches, strong = dbm.count_matches(conn, min_score, strong_th)
        run = dbm.get_run(conn)
        hist = [{"score": s, "count": n} for s, n in dbm.score_histogram(conn)]
    return {
        "matches": matches,
        "strong": strong,
        "min_score": min_score,
        "strong_threshold": strong_th,
        "histogram": hist,
        "run": ({k: run[k] for k in run.keys()} if run is not None else None),
    }


# --------------------------------------------------------------- settings

# The editable surface, in the order a settings screen should show it. Each
# entry names the config key it edits and how to render/validate it. A key NOT
# in this list cannot be written through the app: `cfgwrite` refuses unknown
# keys rather than appending them, and this list is the allow-list on top.
SETTINGS = [
    {"key": "preferences.title_terms", "op": "list", "kind": "str_list",
     "group": "What you are looking for",
     "label": "Job titles you want",
     "help": "Each one becomes a search query at the sources. Add the titles you "
             "would actually take."},
    {"key": "preferences.stack_preferred", "op": "mapping", "kind": "str_int_map",
     "group": "What you are looking for",
     "label": "Skills you care about, and how much",
     "help": "Higher weight = more interested. 3 is a strong preference, "
             "1 is a mild one."},
    {"key": "preferences.title_deny", "op": "list", "kind": "str_list",
     "group": "What you are looking for",
     "label": "Titles to reject",
     "help": "Whole-word match against the title. A posting containing any of "
             "these is filtered out."},

    {"key": "preferences.country_allow", "op": "list", "kind": "str_list",
     "group": "Where",
     "label": "Countries to accept",
     "help": "Two-letter codes, for example de for Germany. Empty means "
             "no country filter."},
    {"key": "preferences.locations_prefer", "op": "list", "kind": "str_list",
     "group": "Where",
     "label": "Places you prefer",
     "help": "Postings here score higher. Substring match, so a city name is "
             "usually enough."},

    {"key": "preferences.min_score", "op": "scalar", "kind": "number",
     "group": "How strict",
     "label": "Minimum score to show",
     "help": "Everything below this is stored but hidden from you."},
    {"key": "preferences.strong_threshold", "op": "scalar", "kind": "number",
     "group": "How strict",
     "label": "Score that counts as a strong match",
     "help": "Shown as the gold band. jobscout stats prints the histogram you "
             "need to pick this from data."},
    {"key": "preferences.max_age_days", "op": "scalar", "kind": "number",
     "group": "How strict",
     "label": "Ignore postings older than this",
     "help": "In days."},
    {"key": "preferences.query_budget", "op": "scalar", "kind": "number",
     "group": "How strict",
     "label": "How many queries per source",
     "help": "Queries are generated from your titles and skills. Higher finds "
             "more and takes longer."},
    {"key": "preferences.stack_needs_title_match", "op": "scalar", "kind": "bool",
     "group": "How strict",
     "label": "Only count a skill when the title mentions it too",
     "help": "On for a narrow search, off to let the description convince you."},
]

_BY_KEY = {s["key"]: s for s in SETTINGS}


def read_settings(path) -> list[dict]:
    """Current value of every editable setting, ready for a settings screen."""
    from . import cfgwrite
    text = pathlib.Path(path).read_text(encoding="utf-8")
    out = []
    for spec in SETTINGS:
        row = dict(spec)
        row["value"] = cfgwrite.get_value(text, spec["key"])
        out.append(row)
    return out


def validate_setting(key: str, value):
    """Check a value from the UI before it reaches the config file.

    The app is a trust boundary: a GUI (or anything speaking this API) can send
    a string where a number belongs, so this validates rather than assuming.
    Returns a normalised value or raises ValueError.
    """
    spec = _BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"not an editable setting: {key!r}")
    kind = spec["kind"]

    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} expects a number, got {type(value).__name__}")
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{key} must be a finite number")
        if key.endswith(("min_score", "strong_threshold")) and not (0 <= value <= 100):
            raise ValueError(f"{key} must be between 0 and 100")
        if key.endswith(("max_age_days", "query_budget")) and not (0 <= int(value) <= 3650):
            raise ValueError(f"{key} must be between 0 and 3650")
        return int(value) if key.endswith(("max_age_days", "query_budget")) else float(value)

    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} expects true/false")
        return value

    if kind == "str_list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{key} expects a list of strings")
        clean = [v.strip() for v in value if v.strip()]
        return clean

    if kind == "str_int_map":
        if not isinstance(value, dict):
            raise ValueError(f"{key} expects an object")
        out = {}
        for k, v in value.items():
            if not isinstance(k, str) or not k.strip():
                raise ValueError("skill names must be non-empty strings")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"weight for {k!r} must be a number")
            out[k.strip()] = int(v)
        return out

    raise ValueError(f"unknown kind {kind!r}")


def write_setting(path, key: str, value) -> dict:
    """Validate, then edit the config file in place, preserving its comments."""
    from . import cfgwrite
    spec = _BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"not an editable setting: {key!r}")
    clean = validate_setting(key, value)
    changed = cfgwrite.edit(path, spec["op"], key, clean)
    return {"key": key, "value": clean, "changed": changed}


# ---------------------------------------------------------------- detail

def show_posting(conn, cfg, pid: int, *, refresh: bool = False) -> dict | None:
    """One posting with everything the detail pane shows.

    Fetches the full description live when asked to (or when the stored one is
    too short to be worth showing), because many boards put only a teaser in
    the listing and the real text behind a second request.
    """
    from . import detail as detailmod
    from .http import Fetcher
    from .models import Posting
    from .sources import REGISTRY

    with dbm.LOCK:
        row = dbm.get_posting(conn, pid)
    if row is None:
        return None

    desc = row["description"] or ""
    if refresh or len(desc) < 400:
        mod = REGISTRY.get(row["source"])
        fetcher = Fetcher()
        posting = Posting(source=row["source"], title=row["title"],
                          company=row["company"], location=row["location"],
                          url=row["url"], external_id=row["external_id"])
        fresh = None
        try:
            fn = getattr(mod, "fetch_detail", None) if mod else None
            if fn is not None:
                fresh = fn(posting, fetcher)
            elif (row["url"] or "").startswith("http"):
                fresh = detailmod.html_detail(row["url"], fetcher)
        except Exception:            # a detail fetch must never break the pane
            fresh = None
        if fresh and len(fresh) > len(desc):
            with dbm.LOCK:
                dbm.set_description(conn, row["id"], fresh)
            desc = fresh

    out = posting_dict(row)
    out["description"] = desc
    out["salary"] = {
        "min": row["salary_min"], "max": row["salary_max"],
        "currency": row["salary_currency"], "period": row["salary_period"],
        "annual_max": row["salary_annual_max"], "raw": row["salary_raw"],
        "text": salmod.fmt_row(row),
    }
    out["liveness"] = {"status": row["verify_status"], "note": row["verify_note"],
                       "checked_at": row["verified_at"]}
    return out
