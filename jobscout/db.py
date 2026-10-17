"""SQLite storage: postings, dedup, runs."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Posting
from .score import Score, prefs_hash, score_posting
from .textutil import norm

# One connection is shared between the CLI (single-threaded) and the GUI, whose
# pywebview `js_api` methods run in SEPARATE THREADS. Two consequences:
#   * `check_same_thread=False` is required or any cross-thread use raises
#     `ProgrammingError: SQLite objects created in a thread can only be used in
#     that same thread`.
#   * Passing that flag alone trades a loud crash for silent corruption, because
#     a sqlite3.Connection is not safe for concurrent use. So every multi-step
#     or writing operation serialises on this lock. Callers that may run on a
#     GUI thread should hold it: `with dbm.LOCK: ...`.
LOCK = threading.RLock()


SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT NOT NULL UNIQUE,
  alt_key TEXT,
  source TEXT NOT NULL,
  company TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  location TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  external_id TEXT NOT NULL DEFAULT '',
  posted_at TEXT NOT NULL DEFAULT '',
  first_seen_at TEXT NOT NULL,
  first_seen_run INTEGER NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_seen_run INTEGER NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  compensation TEXT NOT NULL DEFAULT '',
  salary_min REAL,
  salary_max REAL,
  salary_currency TEXT NOT NULL DEFAULT '',
  salary_period TEXT NOT NULL DEFAULT '',
  salary_annual_max REAL,
  salary_raw TEXT NOT NULL DEFAULT '',
  verify_status TEXT NOT NULL DEFAULT '',
  verify_note TEXT NOT NULL DEFAULT '',
  verified_at TEXT NOT NULL DEFAULT '',
  score REAL NOT NULL DEFAULT 0,
  reasons TEXT NOT NULL DEFAULT '[]',
  filtered INTEGER NOT NULL DEFAULT 0,
  filtered_reason TEXT NOT NULL DEFAULT '',
  dup_of INTEGER,
  status TEXT NOT NULL DEFAULT 'new',
  detail_fetched_at TEXT,
  expires_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_postings_score ON postings(score);
CREATE INDEX IF NOT EXISTS idx_postings_alt ON postings(alt_key);
CREATE INDEX IF NOT EXISTS idx_postings_source ON postings(source);
CREATE INDEX IF NOT EXISTS idx_postings_run ON postings(first_seen_run);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  stats TEXT NOT NULL DEFAULT '{}'
);
"""


_NEW_COLUMNS = [
    ("salary_min", "REAL"),
    ("salary_max", "REAL"),
    ("salary_currency", "TEXT NOT NULL DEFAULT ''"),
    ("salary_period", "TEXT NOT NULL DEFAULT ''"),
    ("salary_annual_max", "REAL"),
    ("salary_raw", "TEXT NOT NULL DEFAULT ''"),
    ("verify_status", "TEXT NOT NULL DEFAULT ''"),
    ("verify_note", "TEXT NOT NULL DEFAULT ''"),
    ("verified_at", "TEXT NOT NULL DEFAULT ''"),
    # Publisher-declared expiry, when the source exposes one. Lets liveness be
    # decided from the feed alone on sources whose HTML sits behind a bot
    # challenge (himalayas), instead of giving up on 800 unverifiable rows.
    ("expires_at", "TEXT NOT NULL DEFAULT ''"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a workspace was created."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(postings)")}
    for name, decl in _NEW_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE postings ADD COLUMN {name} {decl}")
    conn.commit()


def connect(path: Path) -> sqlite3.Connection:
    # check_same_thread=False is required for the GUI (pywebview runs js_api in
    # separate threads). Concurrency is handled by the module LOCK, not by
    # opening more connections -- see the note above LOCK.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    with LOCK:
        conn.executescript(SCHEMA)
        _migrate(conn)
    return conn


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:20]


def canonical_url(url: str) -> str:
    """Strip query/fragment and trailing slash for stable fingerprints."""
    u = (url or "").strip()
    for sep in ("?", "#"):
        i = u.find(sep)
        if i > 0:
            u = u[:i]
    return u.rstrip("/")


def fingerprint(p: Posting) -> str:
    if p.external_id:
        # Identity is source+external_id only: a parser improvement must refresh
        # rows in place, never re-insert them under a new identity.
        return _sha1(f"{p.source}|{p.external_id}")
    if p.url:
        return _sha1(f"{p.source}|{canonical_url(p.url)}")
    return _sha1(f"{p.source}|{norm(p.title)}|{norm(p.company)}|{norm(p.location)}")


def alt_key(p: Posting) -> str | None:
    """Cross-source identity: same company+title+location seen on another board."""
    if not (p.company and p.title):
        return None
    return _sha1(f"{norm(p.company)}|{norm(p.title)}|{norm(p.location)}")


def _same_words(a: str, b: str) -> bool:
    """Do two texts hold the same words, ignoring line breaks and spacing?"""
    return " ".join(a.split()) == " ".join(b.split())


def _prefer_fresh(fresh: str, stored: str) -> bool:
    """Should a re-scan replace the stored description with the fresh parse?

    Longer wins, because the STORED text can be richer than the listing: the
    scan's detail probe writes the full advert in, and overwriting that with a
    teaser would lose text (see the score note in upsert() -- it cost a 16 -> 12
    demotion once).

    Line structure counts as information too, and a plain length test cannot see
    it. The line-preserving extractor produces the SAME LENGTH or a slightly
    SHORTER string than the whitespace-collapsing one for identical content,
    since collapsed whitespace and dropped blank lines are not visible
    characters. Under a bare length test those rows are never refreshed:
    measured on arbeitnow, of 12 live items 0 were longer and 5 were shorter, so
    a re-scan would have repaired none of its 3,044 collapsed rows. When the
    words are identical the structured copy is strictly better.
    """
    if not fresh:
        return False
    if len(fresh) > len(stored):
        return True
    return "\n" in fresh and "\n" not in stored and _same_words(fresh, stored)


def upsert(conn: sqlite3.Connection, p: Posting, sc: Score, run_id: int, now: str,
           prefs: dict | None = None):
    """Insert or refresh a posting.

    Returns (id, is_new, dup_of). ``dup_of`` is set when the same role was
    already seen via a different source (company+title+location match).
    """
    fp = fingerprint(p)
    row = conn.execute(
        "SELECT id, title, company, location, url, posted_at, description, expires_at "
        "FROM postings WHERE fingerprint=?",
        (fp,),
    ).fetchone()
    if row:
        # Refresh derived fields from the fresh parse: re-scanning one source is
        # also the repair path after a parser fix (`jobscout scan --only hn`).
        desc = row["description"] or ""
        if _prefer_fresh(p.description, desc):
            desc = p.description
        # The STORED text can be richer than the fresh parse (the scan's
        # detail probe writes the full advert in). The score must describe
        # what is stored, or every re-scan demotes a promoted row back to
        # its teaser-based points -- measured: a probed row fell 16 -> 12
        # and 13 -> 9 on the next scan before this line existed.
        if prefs and desc and desc != (p.description or ""):
            sc = score_posting(prefs, replace(p, description=desc))
        conn.execute(
            """UPDATE postings SET last_seen_at=?, last_seen_run=?, title=?, company=?, location=?,
                   url=?, posted_at=?, description=?, salary_min=?, salary_max=?, salary_currency=?,
                   salary_period=?, salary_annual_max=?, salary_raw=?, expires_at=?,
                   score=?, reasons=?, filtered=?, filtered_reason=?
               WHERE id=?""",
            (now, run_id, p.title, p.company, p.location,
             p.url or row["url"], p.posted_at or row["posted_at"],
             desc, p.salary_min, p.salary_max, p.salary_currency, p.salary_period,
             p.salary_annual_max, p.salary_raw, p.expires_at or row["expires_at"],
             sc.score, json.dumps(sc.reasons), 1 if sc.filtered else 0, sc.reason, row["id"]),
        )
        conn.commit()
        return row["id"], False, None

    dup_of = None
    ak = alt_key(p)
    if ak:
        r2 = conn.execute(
            "SELECT id FROM postings WHERE alt_key=? AND dup_of IS NULL LIMIT 1",
            (ak,),
        ).fetchone()
        if r2:
            dup_of = r2["id"]

    cur = conn.execute(
        """INSERT INTO postings (fingerprint, alt_key, source, company, title, location, url,
             external_id, posted_at, first_seen_at, first_seen_run, last_seen_at, last_seen_run,
             description, compensation, salary_min, salary_max, salary_currency, salary_period,
             salary_annual_max, salary_raw, expires_at, score, reasons, filtered, filtered_reason, dup_of, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            fp, ak, p.source, p.company, p.title, p.location, p.url, p.external_id, p.posted_at,
            now, run_id, now, run_id, p.description, p.compensation,
            p.salary_min, p.salary_max, p.salary_currency, p.salary_period,
            p.salary_annual_max, p.salary_raw, p.expires_at,
            sc.score, json.dumps(sc.reasons), 1 if sc.filtered else 0, sc.reason, dup_of, "new",
        ),
    )
    conn.commit()
    return cur.lastrowid, True, dup_of


def start_run(conn: sqlite3.Connection) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute("INSERT INTO runs (started_at, stats) VALUES (?, '{}')", (now,))
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, stats: dict) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE runs SET finished_at=?, stats=? WHERE id=?",
        (now, json.dumps(stats, ensure_ascii=False), run_id),
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: int | None = None) -> sqlite3.Row | None:
    if run_id is None:
        return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()


def recent_runs(conn: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def list_postings(conn, min_score=None, status=None, source=None, run_id=None,
                  exclude_filtered=True, include_dups=False, only_filtered=False, limit=200, order="score"):
    where, args = [], []
    if min_score is not None:
        where.append("score >= ?")
        args.append(min_score)
    if status:
        where.append("status = ?")
        args.append(status)
    if source:
        where.append("source = ?")
        args.append(source)
    if run_id is not None:
        where.append("first_seen_run = ?")
        args.append(run_id)
    if exclude_filtered:
        where.append("filtered = 0")
    if only_filtered:
        where.append("filtered = 1")
    if not include_dups:
        where.append("dup_of IS NULL")
    sql = "SELECT * FROM postings"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY score DESC, first_seen_at DESC" if order == "score" else " ORDER BY first_seen_at DESC, score DESC"
    sql += " LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def get_posting(conn, pid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()


def set_status(conn, pid: int, status: str) -> None:
    with LOCK:
        conn.execute("UPDATE postings SET status=? WHERE id=?", (status, pid))
        conn.commit()


def set_description(conn, pid: int, description: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE postings SET description=?, detail_fetched_at=? WHERE id=?",
        (description, now, pid),
    )
    conn.commit()


def set_score(conn, pid: int, sc: Score) -> None:
    conn.execute(
        "UPDATE postings SET score=?, reasons=?, filtered=?, filtered_reason=? WHERE id=?",
        (sc.score, json.dumps(sc.reasons), 1 if sc.filtered else 0, sc.reason, pid),
    )


def all_postings(conn):
    return conn.execute("SELECT * FROM postings ORDER BY id").fetchall()


def counts(conn) -> dict:
    out = {}
    for row in conn.execute("SELECT source, COUNT(*) n FROM postings GROUP BY source ORDER BY n DESC"):
        out[row["source"]] = row["n"]
    return out


def maybe_rescore(conn: sqlite3.Connection, prefs: dict, prev_run) -> tuple[int, int] | None:
    """Rescore everything when the preferences changed since the previous run.

    Returns (total, changed) when a rescore ran, None when nothing needed doing.
    A previous run without a stored prefs hash counts as 'changed' (one-time
    migration for workspaces created before hash tracking existed).
    """
    if prev_run is None:
        return None
    try:
        prev_stats = json.loads(prev_run["stats"] or "{}")
    except (ValueError, TypeError):
        prev_stats = {}
    if prev_stats.get("prefs_hash") == prefs_hash(prefs):
        return None
    return rescore_all(conn, prefs)


def count_matches(conn: sqlite3.Connection, min_score: float, strong_threshold: float) -> tuple[int, int]:
    """(matches, strong) among stored postings under the given thresholds."""
    sql = ("SELECT COUNT(*) FROM postings WHERE filtered=0 AND dup_of IS NULL "
           "AND status<>'dismissed' AND score >= ?")
    matches = conn.execute(sql, (min_score,)).fetchone()[0]
    strong = conn.execute(sql, (strong_threshold,)).fetchone()[0]
    return matches, strong


def score_histogram(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """(score, count) pairs for stored matches, for threshold calibration."""
    rows = conn.execute(
        "SELECT CAST(score AS INTEGER) s, COUNT(*) n FROM postings "
        "WHERE filtered=0 AND dup_of IS NULL AND status<>'dismissed' "
        "GROUP BY s ORDER BY s").fetchall()
    return [(r["s"], r["n"]) for r in rows]


def row_to_posting(row) -> Posting:
    keys = row.keys() if hasattr(row, "keys") else []
    return Posting(
        source=row["source"], title=row["title"], company=row["company"],
        location=row["location"], url=row["url"], external_id=row["external_id"],
        posted_at=row["posted_at"], description=row["description"],
        compensation=row["compensation"],
        salary_min=row["salary_min"] if "salary_min" in keys else None,
        salary_max=row["salary_max"] if "salary_max" in keys else None,
        salary_currency=(row["salary_currency"] if "salary_currency" in keys else "") or "",
        salary_period=(row["salary_period"] if "salary_period" in keys else "") or "",
        salary_annual_max=row["salary_annual_max"] if "salary_annual_max" in keys else None,
        salary_raw=(row["salary_raw"] if "salary_raw" in keys else "") or "",
    )


def update_verify(conn: sqlite3.Connection, pid: int, state: str, note: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute("UPDATE postings SET verify_status=?, verify_note=?, verified_at=? WHERE id=?",
                 (state, note, now, pid))
    conn.commit()


def postings_to_verify(conn: sqlite3.Connection, statuses=("saved", "applied"),
                       max_age_days: int = 3, limit: int = 15):
    """Saved/applied rows whose liveness check is missing or old (oldest first)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat(timespec="seconds")
    marks = ",".join("?" * len(statuses))
    return conn.execute(
        f"SELECT * FROM postings WHERE url <> '' AND status IN ({marks}) "
        "AND (verified_at = '' OR verified_at < ?) ORDER BY verified_at LIMIT ?",
        (*statuses, cutoff, limit),
    ).fetchall()


def rescore_all(conn: sqlite3.Connection, prefs: dict) -> tuple[int, int]:
    """Re-apply the current preferences to every stored posting.

    Returns (total, changed). Used by `jobscout rescore` and automatically by
    `jobscout scan` when it detects the preferences changed.
    """
    total = changed = 0
    for row in all_postings(conn):
        total += 1
        sc = score_posting(prefs, row_to_posting(row))
        if (sc.score != row["score"] or json.dumps(sc.reasons) != (row["reasons"] or "[]")
                or bool(sc.filtered) != bool(row["filtered"])
                or sc.reason != (row["filtered_reason"] or "")):
            set_score(conn, row["id"], sc)
            changed += 1
    conn.commit()
    return total, changed
