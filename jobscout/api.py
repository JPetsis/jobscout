"""Headless service layer — the single contract shared by the CLI and the GUI.

Why this exists: the GUI must not call `cmd_*` functions (they take an
argparse.Namespace, print to stdout, and load the config implicitly), and it
must not re-implement any rule. Everything here returns plain data and defers
to the existing core, so `score.py` / `geo.py` / `salary.py` / `sources/*` stay
the only place the logic lives.

Nothing in this module prints, and nothing writes to the config.
"""
from __future__ import annotations

import json

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
    """
    rows = dbm.list_postings(conn, min_score=min_score, status=status,
                             source=source, limit=limit or 100000)
    out = [posting_dict(r) for r in rows
           if not r["filtered"] and r["dup_of"] is None]
    out.sort(key=lambda r: r["score"], reverse=True)
    if limit:
        out = out[:limit]
    return out


def get_posting(conn, pid: int) -> dict | None:
    row = dbm.get_posting(conn, pid)
    return posting_dict(row) if row is not None else None


def mark_posting(conn, pid: int, status: str) -> dict:
    """Set a posting's triage status. Rejects anything not in VALID_STATUSES.

    A GUI can send an arbitrary string, so validate rather than trusting it.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES!r}, got {status!r}")
    dbm.set_status(conn, int(pid), status)
    return {"id": int(pid), "status": status}


def source_counts(conn) -> list[dict]:
    """Per-source row counts, for the source filter dropdown."""
    return [{"source": s, "count": n} for s, n in (dbm.counts(conn) or {}).items()]


def stats(conn, prefs: dict) -> dict:
    """Header numbers plus the score histogram used to calibrate thresholds."""
    min_score = float(prefs.get("min_score", 6))
    strong_th = float(prefs.get("strong_threshold", 10))
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
