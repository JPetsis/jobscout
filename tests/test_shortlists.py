"""Named shortlists: they outlive the window, and one advert can sit in several."""
from __future__ import annotations

from jobscout import db as dbm, shortlists
from jobscout.models import Posting
from jobscout.score import score_posting

PREFS = {"title_terms": ["software"], "min_score": 0}


def _posting(conn, n: int) -> int:
    run = dbm.start_run(conn)
    p = Posting(source="xing", title=f"Softwareentwickler {n}", company="ACME GmbH",
                location="Berlin", url=f"https://x/{n}", external_id=str(n))
    pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    return int(pid)


def test_the_first_use_makes_one_list_and_only_one(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    first = shortlists.ensure_default(conn)
    second = shortlists.ensure_default(conn)
    assert first == second
    assert [l["name"] for l in shortlists.all_lists(conn)] == ["Shortlist"]


def test_a_posting_can_be_in_two_lists(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    pid = _posting(conn, 1)
    a = shortlists.ensure_default(conn)
    b = int(shortlists.create(conn, "Dream jobs")["id"])
    assert shortlists.add(conn, a, pid)
    assert shortlists.add(conn, b, pid)
    assert shortlists.ids(conn, a) == [pid]
    assert shortlists.ids(conn, b) == [pid]


def test_the_order_is_the_order_you_added(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    sid = shortlists.ensure_default(conn)
    first, second, third = (_posting(conn, i) for i in (1, 2, 3))
    for pid in (third, first, second):
        shortlists.add(conn, sid, pid)
    assert shortlists.ids(conn, sid) == [third, first, second]


def test_toggle_adds_then_removes(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    sid = shortlists.ensure_default(conn)
    pid = _posting(conn, 1)
    assert shortlists.toggle(conn, sid, pid) is True
    assert shortlists.ids(conn, sid) == [pid]
    assert shortlists.toggle(conn, sid, pid) is False
    assert shortlists.ids(conn, sid) == []


def test_deleting_a_list_leaves_the_adverts_alone(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    sid = int(shortlists.create(conn, "Temp")["id"])
    pid = _posting(conn, 1)
    shortlists.add(conn, sid, pid)
    assert shortlists.delete(conn, sid)
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 1
    assert shortlists.ids(conn, sid) == []


def test_the_active_list_survives_a_restart_and_a_deletion(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    first = shortlists.ensure_default(conn)
    second = int(shortlists.create(conn, "Second")["id"])
    assert shortlists.active(conn) == second
    conn.close()

    conn = dbm.connect(tmp_path / "t.db")
    assert shortlists.active(conn) == second
    shortlists.delete(conn, second)
    assert shortlists.active(conn) == first


def test_rename_is_trimmed_and_bounded(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    sid = shortlists.ensure_default(conn)
    shortlists.rename(conn, sid, "   dream   jobs   ")
    assert shortlists.all_lists(conn)[0]["name"] == "dream jobs"
    shortlists.rename(conn, sid, "")
    assert shortlists.all_lists(conn)[0]["name"] == "Shortlist"


def test_counts_follow_membership(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    sid = shortlists.ensure_default(conn)
    for i in (1, 2):
        shortlists.add(conn, sid, _posting(conn, i))
    assert shortlists.count(conn, sid) == 2
    assert shortlists.all_lists(conn)[0]["n"] == 2
