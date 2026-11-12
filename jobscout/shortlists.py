"""Named shortlists: selections that outlive the window.

The grid's + used to build one list in memory, which vanished on quit. A list is
a thing people keep -- "send these to Sara", "dream jobs", "backup" -- so it lives
in the database and a posting can be in several at once.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

DEFAULT_NAME = "Shortlist"
ACTIVE_KEY = "shortlist_active"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _name(text: str) -> str:
    name = " ".join((text or "").split())
    return name[:60] or DEFAULT_NAME


def all_lists(conn: sqlite3.Connection) -> list[dict]:
    """Every list, with how many adverts are in it, newest activity first."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.created_at, s.updated_at, "
        "  (SELECT COUNT(*) FROM shortlist_items i WHERE i.shortlist_id = s.id) AS n "
        "FROM shortlists s ORDER BY s.updated_at DESC, s.id DESC").fetchall()
    return [dict(r) for r in rows]


def ensure_default(conn: sqlite3.Connection) -> int:
    """The list the + adds to. Created on first use, never duplicated."""
    row = conn.execute("SELECT id FROM shortlists ORDER BY id LIMIT 1").fetchone()
    if row is not None:
        return int(row["id"])
    return int(create(conn, DEFAULT_NAME)["id"] or 0)


def create(conn: sqlite3.Connection, name: str) -> dict:
    now = _now()
    cur = conn.execute(
        "INSERT INTO shortlists(name, created_at, updated_at) VALUES (?,?,?)",
        (_name(name), now, now))
    conn.commit()
    sid = int(cur.lastrowid)
    set_active(conn, sid)
    return {"id": sid, "name": _name(name), "n": 0, "updated_at": now}


def rename(conn: sqlite3.Connection, sid: int, name: str) -> bool:
    cur = conn.execute("UPDATE shortlists SET name=?, updated_at=? WHERE id=?",
                       (_name(name), _now(), int(sid)))
    conn.commit()
    return cur.rowcount > 0


def delete(conn: sqlite3.Connection, sid: int) -> bool:
    """Remove a list and its items. The adverts themselves are untouched."""
    sid = int(sid)
    conn.execute("DELETE FROM shortlist_items WHERE shortlist_id=?", (sid,))
    cur = conn.execute("DELETE FROM shortlists WHERE id=?", (sid,))
    conn.commit()
    if cur.rowcount and active(conn) == sid:
        left = conn.execute("SELECT id FROM shortlists ORDER BY id LIMIT 1").fetchone()
        if left is not None:
            set_active(conn, int(left["id"]))
    return cur.rowcount > 0


def active(conn: sqlite3.Connection) -> int:
    """The list the grid's + writes to, kept in meta so it survives a restart."""
    row = conn.execute("SELECT value FROM meta WHERE key=?", (ACTIVE_KEY,)).fetchone()
    sid = 0
    if row is not None:
        try:
            sid = int(row["value"])
        except (TypeError, ValueError):
            sid = 0
    if sid and conn.execute("SELECT 1 FROM shortlists WHERE id=?", (sid,)).fetchone():
        return sid
    return ensure_default(conn)


def set_active(conn: sqlite3.Connection, sid: int) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                 (ACTIVE_KEY, str(int(sid))))
    conn.commit()


def ids(conn: sqlite3.Connection, sid: int) -> list[int]:
    """Posting ids in the order they were added."""
    return ids_in_order(conn, sid)


def ids_in_order(conn: sqlite3.Connection, sid: int) -> list[int]:
    """The list's posting ids in the order they were added."""
    return [int(r["posting_id"]) for r in conn.execute(
        "SELECT posting_id FROM shortlist_items WHERE shortlist_id=? ORDER BY id",
        (int(sid),))]


def add(conn: sqlite3.Connection, sid: int, pid: int) -> bool:
    """True when it was not already there."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO shortlist_items(shortlist_id, posting_id, added_at) "
        "VALUES (?,?,?)", (int(sid), int(pid), _now()))
    if cur.rowcount:
        conn.execute("UPDATE shortlists SET updated_at=? WHERE id=?",
                     (_now(), int(sid)))
    conn.commit()
    return bool(cur.rowcount)


def remove(conn: sqlite3.Connection, sid: int, pid: int) -> bool:
    cur = conn.execute("DELETE FROM shortlist_items WHERE shortlist_id=? AND posting_id=?",
                       (int(sid), int(pid)))
    if cur.rowcount:
        conn.execute("UPDATE shortlists SET updated_at=? WHERE id=?",
                     (_now(), int(sid)))
    conn.commit()
    return bool(cur.rowcount)


def toggle(conn: sqlite3.Connection, sid: int, pid: int) -> bool:
    """Flip membership. Returns True when the advert is now in the list."""
    if int(pid) in ids(conn, sid):
        remove(conn, sid, pid)
        return False
    add(conn, sid, pid)
    return True


def chosen(conn: sqlite3.Connection, sid: int, ids=None) -> list[int]:
    """The adverts to export: the ones asked for, in the list's own order.

    No ids means the whole list. A selection that matches nothing also means the
    whole list, because an empty PDF is never what anybody wanted.
    """
    everything = ids_in_order(conn, sid)
    wanted = {int(i) for i in (ids or []) if str(i).lstrip("-").isdigit()}
    if not wanted:
        return everything
    picked = [pid for pid in everything if pid in wanted]
    return picked or everything


def count(conn: sqlite3.Connection, sid: int) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM shortlist_items WHERE shortlist_id=?",
                            (int(sid),)).fetchone()[0])
