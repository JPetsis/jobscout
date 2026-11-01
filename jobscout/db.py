"""SQLite storage: postings, dedup, runs."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import Posting
from .score import Score, prefs_hash, score_posting
from . import geo
from .textutil import norm, strip_null_runs

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
  deadline_at TEXT NOT NULL DEFAULT '',
  deadline_note TEXT NOT NULL DEFAULT '',
  early_applicant INTEGER NOT NULL DEFAULT 0,
  detail_fetched_at TEXT,
  expires_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_postings_score ON postings(score);
CREATE INDEX IF NOT EXISTS idx_postings_alt ON postings(alt_key);
CREATE INDEX IF NOT EXISTS idx_postings_source ON postings(source);
CREATE INDEX IF NOT EXISTS idx_postings_run ON postings(first_seen_run);
-- Every "has another board carried this yet" lookup joins on dup_of, and the
-- grid asks for a whole page of them at once: without this it is a scan per
-- row. The fresh-source mark and the lead-time report both need it.
CREATE INDEX IF NOT EXISTS idx_postings_dup ON postings(dup_of);

-- Full text over the advert prose, which is most of what this store holds and
-- the only part of it nothing could search. External content: the text lives in
-- postings, the index here, and the triggers keep the two in step.
CREATE VIRTUAL TABLE IF NOT EXISTS postings_fts USING fts5(
  description,
  content='postings',
  content_rowid='id',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS postings_fts_ai AFTER INSERT ON postings BEGIN
  INSERT INTO postings_fts(rowid, description) VALUES (new.id, new.description);
END;
CREATE TRIGGER IF NOT EXISTS postings_fts_ad AFTER DELETE ON postings BEGIN
  INSERT INTO postings_fts(postings_fts, rowid, description)
    VALUES ('delete', old.id, old.description);
END;
CREATE TRIGGER IF NOT EXISTS postings_fts_au AFTER UPDATE ON postings BEGIN
  INSERT INTO postings_fts(postings_fts, rowid, description)
    VALUES ('delete', old.id, old.description);
  INSERT INTO postings_fts(rowid, description) VALUES (new.id, new.description);
END;

-- Small key/value corner. It exists because an FTS5 table with external content
-- cannot tell you whether its index was ever built: a count over it reads the
-- CONTENT table, so an empty index looks full. One row here records what has
-- been built, which is cheaper and clearer than sniffing shadow tables.
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  stats TEXT NOT NULL DEFAULT '{}'
);

-- What YOU did about a posting, kept apart from what the boards said.
--
-- A posting row is the advert: it can expire, be re-published under a new id,
-- or be deduplicated into another row. An application is a fact about the
-- person and must survive all of that, so it has its own row keyed by
-- posting_id (one application per posting; applying twice to the same advert
-- is the same relationship, and a second round is a stage change).
--
-- `stage` is where it is now; `outcome` is how it ended, and is empty while it
-- is still running. Both are plain text rather than a join table: the vocabu-
-- lary is small, fixed, and validated at the edge, and a lookup table would add
-- a join to every read for no behaviour.
CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  posting_id INTEGER NOT NULL UNIQUE,
  applied_at TEXT NOT NULL DEFAULT '',
  stage TEXT NOT NULL DEFAULT 'applied',
  outcome TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '',
  salary_advertised TEXT NOT NULL DEFAULT '',
  contact TEXT NOT NULL DEFAULT '',
  follow_up_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_app_stage ON applications(stage);
CREATE INDEX IF NOT EXISTS idx_app_applied ON applications(applied_at);
CREATE INDEX IF NOT EXISTS idx_app_follow ON applications(follow_up_at);

-- The journey, append-only: the application, every stage move, notes,
-- follow-ups.
-- Never rewritten, so "when did I hear back" stays answerable.
CREATE TABLE IF NOT EXISTS application_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_app_events ON application_events(application_id, at);
"""


_NEW_COLUMNS = [
    ("deadline_at", "TEXT NOT NULL DEFAULT ''"),
    ("deadline_note", "TEXT NOT NULL DEFAULT ''"),
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
    # A nudge, not a fact about the job: "be among the first applicants" is what
    # the board said when it was read, so it can only be set, never cleared by a
    # later listing that simply did not repeat it.
    ("early_applicant", "INTEGER NOT NULL DEFAULT 0"),
    # The advert's language, filled when `language_allow` is on. Stored so the
    # window can say WHY a posting was filtered, and so a re-score does not have
    # to reclassify text that has not changed.
    ("lang", "TEXT NOT NULL DEFAULT ''"),
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


SCAN_STAMP = "scan_at:"


def mark_source_scanned(conn, name: str, when: str) -> None:
    """Remember a successful fetch of one source, for the freshness gate."""
    with LOCK:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                     (SCAN_STAMP + name, when))
        conn.commit()


def sources_scanned(conn) -> dict[str, str]:
    """{source: last successful scan time} for every source we have a stamp for."""
    with LOCK:
        return {r["key"][len(SCAN_STAMP):]: r["value"]
                for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE ?",
                                      (SCAN_STAMP + "%",))}


HEALTH_KEY = "health:"


def note_source_health(conn, name: str, fetched: int, new: int = 0,
                       error: str = "", when: str = "") -> None:
    """Remember what one board did on its last run.

    The failure this exists for is silent: a board answering HTTP 200 with zero
    results looks identical to a board with no vacancies. `zero_streak` is the
    tell, and `last_ok_fetched` is what it used to return.
    """
    when = when or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LOCK:
        row = conn.execute("SELECT value FROM meta WHERE key=?",
                           (HEALTH_KEY + name,)).fetchone()
        try:
            cur = json.loads(row["value"]) if row and row["value"] else {}
        except ValueError:
            cur = {}
        streak = int(cur.get("zero_streak") or 0)
        if fetched:
            streak = 0
            cur["last_ok"] = when
            cur["last_ok_fetched"] = int(fetched)
        else:
            streak += 1
        cur.update({"last_run": when, "last_fetched": int(fetched),
                    "last_new": int(new), "last_error": (error or "")[:300],
                    "zero_streak": streak})
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                     (HEALTH_KEY + name, json.dumps(cur)))
        conn.commit()


def source_health(conn) -> dict[str, dict]:
    """{source: health record} for every board that has been run."""
    with LOCK:
        rows = conn.execute("SELECT key, value FROM meta WHERE key LIKE ?",
                            (HEALTH_KEY + "%",)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        try:
            out[r["key"][len(HEALTH_KEY):]] = json.loads(r["value"])
        except (ValueError, TypeError):
            continue
    return out


def closing_soon(conn, days: int, limit: int = 60) -> list:
    """Postings whose advert states a deadline within `days`, soonest first.

    LEFT JOIN so a row that already has an application comes back marked: the
    question the caller asks is "what closes that I have not applied to".
    """
    with LOCK:
        return conn.execute(
            "SELECT p.id, p.title, p.company, p.location, p.deadline_at, "
            "       p.score, p.source, p.url, a.id AS application_id "
            "FROM postings p LEFT JOIN applications a ON a.posting_id = p.id "
            "WHERE p.dup_of IS NULL AND p.filtered = 0 AND p.deadline_at <> '' "
            "  AND date(p.deadline_at) >= date('now') "
            "  AND date(p.deadline_at) <= date('now', ?) "
            "ORDER BY date(p.deadline_at) ASC LIMIT ?",
            (f"+{int(days)} days", int(limit))).fetchall()


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
    """Cross-source identity: same company+title+location seen on another board.

    The location goes through `geo.place_key`, because boards spell one city
    differently ("Mülheim (Ruhr)" against "Mülheim an der Ruhr") and a verbatim
    comparison treats the same advert as two unrelated roles.
    """
    if not (p.company and p.title):
        return None
    return _sha1(f"{norm(p.company)}|{norm(p.title)}|{geo.place_key(p.location)}")


def _same_source_twin(conn: sqlite3.Connection, p: Posting) -> int | None:
    """An existing row from the SAME board that is the same role as `p`.

    Boards republish: XING carried one Landesverwaltungsamt role twice, under
    "…amt Berlin" and "…amt Berlin Personalservice", with the title cut at 100
    characters the second time and a fresh posting id. `fingerprint` cannot see
    that (it is source+id by design) and `alt_key` cannot either (company and
    title both differ), so the same job sat on the board twice.

    The rule is deliberately narrow: same source, same location, one title a
    prefix of the other (both long enough to mean something), and the same for
    the company. A shared prefix alone is not evidence.
    """
    if not (p.title and p.company and p.location):
        return None
    rows = conn.execute(
        "SELECT id, title, company FROM postings "
        "WHERE source = ? AND dup_of IS NULL AND status <> 'dismissed' "
        "AND lower(location) = lower(?)",
        (p.source, p.location),
    ).fetchall()
    title, company = norm(p.title), norm(p.company)
    if len(title) < 30 or len(company) < 4:
        return None
    for r in rows:
        other_title, other_company = norm(r["title"]), norm(r["company"])
        if len(other_title) < 30:
            continue
        if not (title.startswith(other_title) or other_title.startswith(title)):
            continue
        if company.startswith(other_company) or other_company.startswith(company):
            return r["id"]
    return None


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
        desc = strip_null_runs(desc)
        # The STORED text can be richer than the fresh parse (the scan's
        # detail probe writes the full advert in). The score must describe
        # what is stored, or every re-scan demotes a promoted row back to
        # its teaser-based points -- measured: a probed row fell 16 -> 12
        # and 13 -> 9 on the next scan before this line existed.
        if prefs and desc and desc != (p.description or ""):
            sc = score_posting(prefs, replace(p, description=desc))
        # Seen in a board's listing means open, as of now, for free. It also
        # revives a row that was marked dead and has been re-advertised: before
        # this, `verify_status` was never touched by a scan, so a re-posted role
        # stayed "⚠ dead" for good.
        conn.execute(
            """UPDATE postings SET last_seen_at=?, last_seen_run=?, title=?, company=?, location=?,
                   url=?, posted_at=?, description=?, salary_min=?, salary_max=?, salary_currency=?,
                   salary_period=?, salary_annual_max=?, salary_raw=?, expires_at=?,
                   early_applicant=MAX(early_applicant, ?),
                   verify_status='ok', verify_note='', verified_at=?,
                   score=?, reasons=?, filtered=?, filtered_reason=?, lang=?
               WHERE id=?""",
            (now, run_id, p.title, p.company, p.location,
             p.url or row["url"], p.posted_at or row["posted_at"],
             desc, p.salary_min, p.salary_max, p.salary_currency, p.salary_period,
             p.salary_annual_max, p.salary_raw, p.expires_at or row["expires_at"],
             1 if p.early_applicant else 0, now,
             sc.score, json.dumps(sc.reasons), 1 if sc.filtered else 0, sc.reason,
             sc.lang, row["id"]),
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
    if dup_of is None:
        dup_of = _same_source_twin(conn, p)

    cur = conn.execute(
        """INSERT INTO postings (fingerprint, alt_key, source, company, title, location, url,
             external_id, posted_at, first_seen_at, first_seen_run, last_seen_at, last_seen_run,
             description, compensation, salary_min, salary_max, salary_currency, salary_period,
             salary_annual_max, salary_raw, expires_at, early_applicant,
             verify_status, verified_at,
             score, reasons, filtered, filtered_reason, lang, dup_of, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            fp, ak, p.source, p.company, p.title, p.location, p.url, p.external_id, p.posted_at,
            now, run_id, now, run_id, strip_null_runs(p.description), p.compensation,
            p.salary_min, p.salary_max, p.salary_currency, p.salary_period,
            p.salary_annual_max, p.salary_raw, p.expires_at,
            1 if p.early_applicant else 0,
            "ok", now,                  # a posting a board just returned is open
            sc.score, json.dumps(sc.reasons), 1 if sc.filtered else 0, sc.reason,
            sc.lang, dup_of, "new",
        ),
    )
    conn.commit()
    return cur.lastrowid, True, dup_of


FTS_STAMP = "fts_built"


def ensure_fts(conn: sqlite3.Connection) -> bool:
    """Build the full-text index if this store has never had one. True if built.

    One-time and reported, because it is not free: the store holds tens of
    megabytes of advert text, and a silent multi-second pause on the first
    connect after an upgrade is the kind of thing that looks like a hang.
    """
    with LOCK:
        done = conn.execute("SELECT value FROM meta WHERE key=?", (FTS_STAMP,)).fetchone()
        if done is not None:
            return False
        conn.execute("INSERT INTO postings_fts(postings_fts) VALUES('rebuild')")
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                     (FTS_STAMP, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        conn.commit()
    return True


def _fts_query(text: str) -> str:
    """What a person typed, as an FTS5 query that cannot be a syntax error.

    MATCH has its own language -- quotes, parentheses, NEAR, AND/OR/NOT -- and
    passing user text straight through turns a stray bracket into an exception
    from deep inside sqlite. Each word becomes a quoted prefix term, so partial
    words match and nothing else is interpreted.
    """
    words = re.findall(r"[\w\u00c0-\u024f]+", text or "", flags=re.UNICODE)
    return " AND ".join(f'"{w}"*' for w in words if len(w) > 1)


def search_text(conn: sqlite3.Connection, text: str, limit: int = 200,
                include_hidden: bool = False) -> list[dict]:
    """Postings whose advert text matches, with the matched words shown.

    Builds the index if this store has never had one. That is deliberate and it
    belongs here rather than in `connect()`: it costs a second or two on a real
    store, and nobody should pay it unless they actually search. The reason it
    cannot be skipped is that an FTS5 table with external content cannot tell
    you its index is empty -- a query over it honestly reads the CONTENT table
    and finds every posting, so the first search would look like it worked and
    silently return nothing.

    Hidden rows (filtered out, or duplicates of another board's copy) are left
    out by default, because the count has to describe what the window can SHOW:
    "82 postings match" next to two visible cards is the kind of number that
    makes a search look broken. `include_hidden` is for the CLI's `--filtered`,
    which exists to inspect exactly those rows.

    Returns [] rather than raising for a query with nothing searchable in it: an
    empty box and a box full of punctuation are the same request, and neither is
    an error.
    """
    query = _fts_query(text)
    if not query:
        return []
    ensure_fts(conn)
    with LOCK:
        visible = "" if include_hidden else " AND p.filtered = 0 AND p.dup_of IS NULL"
        rows = conn.execute(
            "SELECT p.id, "
            "       snippet(postings_fts, 0, '', '', ' … ', 12) AS around, "
            "       bm25(postings_fts) AS rank "
            "FROM postings_fts JOIN postings p ON p.id = postings_fts.rowid "
            "WHERE postings_fts MATCH ?" + visible + " "
            "ORDER BY rank LIMIT ?", (query, int(limit))).fetchall()
    return [{"id": r["id"], "around": (r["around"] or "").strip(), "rank": r["rank"]}
            for r in rows]


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


def set_deadline(conn: sqlite3.Connection, pid: int, when: str, note: str) -> None:
    """Record an extracted deadline, and the words it came from."""
    with LOCK:
        conn.execute("UPDATE postings SET deadline_at=?, deadline_note=? WHERE id=?",
                     (when or "", (note or "")[:400], int(pid)))
        conn.commit()


def set_description(conn, pid: int, description: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE postings SET description=?, detail_fetched_at=? WHERE id=?",
        (description, now, pid),
    )
    conn.commit()


def dedupe_same_source(conn: sqlite3.Connection) -> int:
    """Mark republished rows from one board as duplicates. Returns how many.

    Run as one pass after a scan rather than per upsert: the comparison is
    against other rows of the same board and place, so doing it for 45,000
    upserts would repeat the same work thousands of times.
    """
    groups: dict[tuple[str, str], list] = {}
    rows = conn.execute(
        "SELECT id, source, title, company, location FROM postings "
        "WHERE dup_of IS NULL AND status <> 'dismissed' ORDER BY id"
    ).fetchall()
    marked = 0
    for r in rows:
        title, company = norm(r["title"]), norm(r["company"])
        if len(title) < 30 or len(company) < 4:
            continue
        bucket = groups.setdefault((r["source"], norm(r["location"])), [])
        twin = None
        for other in bucket:
            o_title, o_company = other["title"], other["company"]
            if not (title.startswith(o_title) or o_title.startswith(title)):
                continue
            if company.startswith(o_company) or o_company.startswith(company):
                twin = other["id"]
                break
        if twin is not None:
            conn.execute("UPDATE postings SET dup_of=? WHERE id=?", (twin, r["id"]))
            marked += 1
            continue                     # a duplicate is not a yardstick itself
        bucket.append({"id": r["id"], "title": title, "company": company})
    if marked:
        conn.commit()
    return marked


def dedupe_cross_source(conn: sqlite3.Connection, direct=()) -> int:
    """Boards advertising one post: keep a single row. Returns how many went.

    Deliberately narrow. It merges only when the title is IDENTICAL after
    normalisation, the place matches and one company name contains the other,
    because a wrong merge hides a job the user could have applied to. It caught
    the Cambridge museum directorship, advertised with the same wording by
    jobs.ac.uk and THE, from two different boards.

    It does NOT merge translations. A German and an English advert for the same
    chair share no word, and the fields that survive translation — employer,
    city, grade (W1/W2/W3), even the publication date — are shared by every
    other chair the same university is advertising that month. Tried on the real
    Kiel store, that rule merged two genuinely different W2 chairs into a third
    one, so the honest answer is to pick one board per market rather than to
    guess. See `sources/__init__.py` for what each board covers.

    The employer's own board wins when one of the rows came from it; otherwise
    the row seen first stays, so the original advert keeps its id and history.
    """
    groups: dict[tuple[str, str], list] = {}
    rows = conn.execute(
        "SELECT id, source, title, company, location FROM postings "
        "WHERE dup_of IS NULL AND status <> 'dismissed' ORDER BY id"
    ).fetchall()
    for r in rows:
        # boards disagree about how much of the address to print ("Cambridge"
        # vs "Cambridge, United Kingdom"), so only the first segment is compared
        place = norm((r["location"] or "").split(",")[0])
        title, company = norm(r["title"]), norm(r["company"])
        if len(title) < 30 or len(company) < 6 or not place:
            continue
        groups.setdefault((place, title), []).append(
            {"id": r["id"], "source": r["source"], "company": company})
    marked = 0
    for items in groups.values():
        if len(items) < 2:
            continue
        keeper = next((it for it in items if it["source"] in direct), items[0])
        for it in items:
            if it is keeper:
                continue
            a, b = keeper["company"], it["company"]
            if a.startswith(b) or b.startswith(a):
                conn.execute("UPDATE postings SET dup_of=? WHERE id=?",
                             (keeper["id"], it["id"]))
                marked += 1
    if marked:
        conn.commit()
    return marked


def purge_dead(conn: sqlite3.Connection, keep_statuses=("saved", "applied")) -> int:
    """Delete postings a check found gone. Returns how many went.

    A dead advert has no use on a board, but two kinds of row are not just an
    advert: one the user saved or applied to, and one an application points at.
    The Applications pane joins on `posting_id`, so deleting that row would
    erase what the user did, which is the record the logger exists to keep.
    """
    marks = ",".join("?" * len(keep_statuses))
    cur = conn.execute(
        f"DELETE FROM postings WHERE verify_status = 'dead' "
        f"AND status NOT IN ({marks}) "
        "AND id NOT IN (SELECT posting_id FROM applications)",
        (*keep_statuses,),
    )
    conn.commit()
    return cur.rowcount


def set_early(conn, pid: int) -> None:
    """Remember that the board called this an early-applicant posting.

    Only ever sets the flag: a later listing that does not repeat the line says
    nothing, and clearing it would turn "we were not told" into "we were told
    it is not".
    """
    conn.execute("UPDATE postings SET early_applicant=1 WHERE id=?", (pid,))
    conn.commit()


def set_score(conn, pid: int, sc: Score) -> None:
    """Store a score. Commits, like every other setter.

    It used to leave the write to whichever commit happened next, and a caller
    that ended straight after it (a repair script, a probe that only changed the
    score) lost the update when the connection closed: the ALDI XING row sat at
    its teaser score of 14 in the database while the rescan that had just run
    read 19.
    """
    conn.execute(
        "UPDATE postings SET score=?, reasons=?, filtered=?, filtered_reason=?, lang=? "
        "WHERE id=?",
        (sc.score, json.dumps(sc.reasons), 1 if sc.filtered else 0, sc.reason,
         sc.lang, pid),
    )
    conn.commit()


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
        early_applicant=bool(row["early_applicant"]) if "early_applicant" in keys else False,
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
    # A row seen by a scan now carries verified_at, so "0 days" has to mean
    # "check it anyway" rather than "older than this instant", which nothing is.
    marks = ",".join("?" * len(statuses))
    if max_age_days <= 0:
        return conn.execute(
            f"SELECT * FROM postings WHERE url <> '' AND status IN ({marks}) "
            "ORDER BY verified_at LIMIT ?", (*statuses, limit),
        ).fetchall()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)
              ).isoformat(timespec="seconds")
    return conn.execute(
        f"SELECT * FROM postings WHERE url <> '' AND status IN ({marks}) "
        "AND (verified_at = '' OR verified_at < ?) ORDER BY verified_at LIMIT ?",
        (*statuses, cutoff, limit),
    ).fetchall()


def postings_to_check(conn: sqlite3.Connection, min_score: float, *,
                      statuses=("new", "saved", "applied"), max_age_days: int = 3,
                      limit: int | None = None) -> list:
    """Rows worth a liveness check: the ones the board can actually show.

    Scope matters more than speed here. A real store held 45,224 postings and
    1,449 of them above the score floor; checking the other 43,775 costs hours
    and answers a question nobody asked. Highest score first, so a pass that is
    stopped early has covered what the user is most likely to open.
    """
    marks = ",".join("?" * len(statuses))
    sql = ("SELECT * FROM postings WHERE url <> '' AND filtered=0 AND dup_of IS NULL "
           f"AND status IN ({marks}) AND score >= ? ")
    params: tuple = (*statuses, float(min_score))
    if max_age_days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)
                  ).isoformat(timespec="seconds")
        sql += "AND (verified_at = '' OR verified_at < ?) "
        params += (cutoff,)
    sql += "ORDER BY score DESC, verified_at"
    if limit:
        sql += " LIMIT ?"
        params += (int(limit),)
    return conn.execute(sql, params).fetchall()


def rescore_all(conn: sqlite3.Connection, prefs: dict, progress=None) -> tuple[int, int]:
    """Re-apply the current preferences to every stored posting.

    Returns (total, changed). Used by `jobscout rescore`, by `jobscout scan` when
    it detects the preferences changed, and by the window's re-score button.

    `progress(done, total, changed)` is called every few hundred rows: a real
    store of 38,000 postings takes about 25 seconds, so a caller with a window
    has to be able to say how far it has got.
    """
    rows = all_postings(conn)
    total = len(rows)
    changed = 0
    for i, row in enumerate(rows, 1):
        sc = score_posting(prefs, row_to_posting(row))
        if (sc.score != row["score"] or json.dumps(sc.reasons) != (row["reasons"] or "[]")
                or bool(sc.filtered) != bool(row["filtered"])
                or sc.reason != (row["filtered_reason"] or "")
                or (sc.lang or "") != (row["lang"] or "")):
            set_score(conn, row["id"], sc)
            changed += 1
        if progress is not None and (i % 250 == 0 or i == total):
            progress(i, total, changed)
    conn.commit()
    return total, changed

# ------------------------------------------------------------- applications

#: Where an application is in the process. Terminal states live in OUTCOMES.
STAGES = ("applied", "screening", "interview", "task", "offer")
#: How it ended. "" means still running.
OUTCOMES = ("", "hired", "declined", "rejected", "withdrawn", "no_reply")


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_application(conn, posting_id: int):
    """This posting's application row, or None."""
    with LOCK:
        return conn.execute("SELECT * FROM applications WHERE posting_id = ?",
                            (int(posting_id),)).fetchone()


def application_of_many(conn, posting_ids) -> dict[int, sqlite3.Row]:
    """{posting_id: row} for the grid, in one query rather than one per card."""
    ids = [int(i) for i in posting_ids]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with LOCK:
        rows = conn.execute(
            f"SELECT * FROM applications WHERE posting_id IN ({marks})", ids).fetchall()
    return {r["posting_id"]: r for r in rows}


def application_timeline(conn, application_id: int) -> list[sqlite3.Row]:
    with LOCK:
        return conn.execute(
            "SELECT * FROM application_events WHERE application_id = ? ORDER BY at, id",
            (int(application_id),)).fetchall()


def _add_event(conn, application_id: int, kind: str, detail: str = "", at: str = "") -> None:
    conn.execute(
        "INSERT INTO application_events(application_id, at, kind, detail) VALUES (?,?,?,?)",
        (int(application_id), at or _stamp(), kind, detail))


def save_application(conn, posting_id: int, fields: dict) -> sqlite3.Row:
    """Create or update the application for a posting, and log what changed.

    Only real changes are logged: saving the same form twice must not fill the
    history with noise, or the one line that matters ("offer") gets lost in it.
    """
    with LOCK:
        row = conn.execute("SELECT * FROM applications WHERE posting_id = ?",
                           (int(posting_id),)).fetchone()
        now = _stamp()
        if row is None:
            values = {
                "posting_id": int(posting_id),
                "applied_at": str(fields.get("applied_at") or now[:10]),
                "stage": str(fields.get("stage") or "applied"),
                "outcome": str(fields.get("outcome") or ""),
                "channel": str(fields.get("channel") or ""),
                "salary_advertised": str(fields.get("salary_advertised") or ""),
                "contact": str(fields.get("contact") or ""),
                "follow_up_at": str(fields.get("follow_up_at") or ""),
                "created_at": now,
                "updated_at": now,
            }
            cur = conn.execute(
                "INSERT INTO applications(posting_id, applied_at, stage, outcome, channel,"
                " salary_advertised, contact, follow_up_at, created_at, updated_at)"
                " VALUES (:posting_id, :applied_at, :stage, :outcome, :channel,"
                " :salary_advertised, :contact, :follow_up_at, :created_at, :updated_at)",
                values)
            # One opening event, carrying HOW it was sent. The starting stage
            # is already a column, so repeating it here would make the history
            # say "applied" and "screening" for the same moment.
            _add_event(conn, cur.lastrowid, "applied", values["channel"])
            conn.commit()
            return conn.execute("SELECT * FROM applications WHERE id = ?",
                                (cur.lastrowid,)).fetchone()

        changes = {}
        for key in ("applied_at", "stage", "outcome", "channel", "salary_advertised",
                    "contact", "follow_up_at"):
            if key not in fields:
                continue
            value = str(fields.get(key) or "")
            if value != str(row[key] or ""):
                changes[key] = value
        if not changes:
            return row
        changes["updated_at"] = now
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE applications SET {sets} WHERE id = ?",
                     (*changes.values(), row["id"]))
        if "stage" in changes:
            _add_event(conn, row["id"], "stage", changes["stage"])
        if "outcome" in changes:
            _add_event(conn, row["id"], "outcome", changes["outcome"] or "open again")
        if "follow_up_at" in changes and changes["follow_up_at"]:
            _add_event(conn, row["id"], "follow_up", changes["follow_up_at"])
        conn.commit()
        return conn.execute("SELECT * FROM applications WHERE id = ?",
                            (row["id"],)).fetchone()


def add_application_note(conn, posting_id: int, text: str) -> None:
    """A dated line in the history: what was said, who said it, what happens next."""
    text = (text or "").strip()
    if not text:
        return
    with LOCK:
        row = conn.execute("SELECT id FROM applications WHERE posting_id = ?",
                           (int(posting_id),)).fetchone()
        if row is None:
            return
        _add_event(conn, row["id"], "note", text[:2000])
        conn.execute("UPDATE applications SET updated_at = ? WHERE id = ?",
                     (_stamp(), row["id"]))
        conn.commit()


def delete_application(conn, posting_id: int) -> bool:
    """Remove an application and its history (the posting stays)."""
    with LOCK:
        row = conn.execute("SELECT id FROM applications WHERE posting_id = ?",
                           (int(posting_id),)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM application_events WHERE application_id = ?", (row["id"],))
        conn.execute("DELETE FROM applications WHERE id = ?", (row["id"],))
        conn.commit()
        return True


def list_applications(conn, *, open_only: bool = False, limit: int = 1000):
    """Applications joined with the advert they were sent to, newest first."""
    where = ["1 = 1"]
    if open_only:
        where.append("a.outcome = ''")
    with LOCK:
        return conn.execute(
            "SELECT a.*, p.title, p.company, p.location, p.url, p.source AS posting_source,"
            " p.score, p.salary_min, p.salary_max, p.salary_currency, p.salary_period,"
            " p.salary_raw, p.compensation, p.status AS posting_status"
            " FROM applications a JOIN postings p ON p.id = a.posting_id"
            f" WHERE {' AND '.join(where)}"
            " ORDER BY a.applied_at DESC, a.id DESC LIMIT ?", (int(limit),)).fetchall()


def application_counts(conn) -> dict:
    """Numbers for the header: how many, how many still running, how many replied."""
    with LOCK:
        row = conn.execute(
            "SELECT count(*) AS total,"
            " sum(CASE WHEN outcome = '' THEN 1 ELSE 0 END) AS open,"
            " sum(CASE WHEN outcome IN ('hired','declined') THEN 1 ELSE 0 END) AS offers,"
            " sum(CASE WHEN outcome = 'rejected' THEN 1 ELSE 0 END) AS rejected,"
            " sum(CASE WHEN outcome IN ('withdrawn','no_reply') THEN 1 ELSE 0 END) AS lost,"
            " sum(CASE WHEN outcome = '' AND follow_up_at <> '' AND follow_up_at <= date('now')"
            "          THEN 1 ELSE 0 END) AS due,"
            " sum(CASE WHEN stage <> 'applied' OR outcome <> '' THEN 1 ELSE 0 END) AS replied"
            " FROM applications").fetchone()
    return {k: int(row[k] or 0) for k in row.keys()}
