"""The job logger: what you did about a posting, kept apart from the advert.

The rules worth pinning: an application is one per posting, its history is
append-only, only real changes are logged, and applying is also triage (the
posting's status becomes "applied") so the grid and the logger cannot disagree.
"""
from datetime import datetime, timezone

import pytest

from jobscout import api
from jobscout import db as dbm


@pytest.fixture()
def conn(tmp_path):
    return dbm.connect(tmp_path / "db.sqlite")


def _posting(conn, title="Backend Engineer", company="ACME", salary=(60000, 80000)):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO postings(fingerprint, source, company, title, location, url,"
        " external_id, first_seen_at, first_seen_run, last_seen_at, last_seen_run,"
        " score, reasons, salary_min, salary_max, salary_currency, salary_period,"
        " compensation)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"fp-{title}-{company}", "linkedin", company, title, "Berlin",
         "https://example.com/1", "1", now, 1, now, 1, 12.0, "[]",
         salary[0], salary[1], "EUR", "year", "60.000 - 80.000 EUR"))
    conn.commit()
    return int(cur.lastrowid)


def test_marking_applied_creates_one_row_and_logs_it(conn):
    pid = _posting(conn)
    out = api.save_application(conn, pid, {"applied_at": "2026-10-20", "channel": "linkedin",
                                           "contact": "Nina (HR)"})
    assert out["posting_id"] == pid
    assert out["stage"] == "applied" and out["outcome"] == ""
    assert out["applied_at"] == "2026-10-20"
    assert out["contact"] == "Nina (HR)"
    assert [e["kind"] for e in out["timeline"]] == ["applied"]
    # the advert's band is offered as the advertised salary, not invented
    assert "60k" in out["salary_from_posting"], out["salary_from_posting"]


def test_applying_also_marks_the_posting_applied(conn):
    pid = _posting(conn)
    api.save_application(conn, pid, {})
    assert dbm.get_posting(conn, pid)["status"] == "applied"


def test_saving_the_same_values_twice_does_not_fill_the_history(conn):
    pid = _posting(conn)
    api.save_application(conn, pid, {"stage": "screening"})
    api.save_application(conn, pid, {"stage": "screening"})
    out = api.get_application(conn, pid)
    assert [e["kind"] for e in out["timeline"]] == ["applied"]


def test_each_stage_move_is_kept_in_order(conn):
    pid = _posting(conn)
    api.save_application(conn, pid, {"stage": "screening"})
    api.save_application(conn, pid, {"stage": "interview"})
    api.save_application(conn, pid, {"outcome": "rejected"})
    kinds = [(e["kind"], e["detail"]) for e in api.get_application(conn, pid)["timeline"]]
    # the opening entry carries HOW it was sent, not the stage: the stage is a
    # column, and repeating it would date the same moment twice
    assert kinds[0] == ("applied", "")
    assert ("stage", "interview") in kinds
    assert ("outcome", "rejected") in kinds


def test_a_note_is_dated_and_kept(conn):
    pid = _posting(conn)
    api.save_application(conn, pid, {})
    out = api.add_application_note(conn, pid, "Recruiter said the team is 6 people.")
    notes = [e for e in out["timeline"] if e["kind"] == "note"]
    assert len(notes) == 1
    assert "6 people" in notes[0]["detail"]
    assert notes[0]["at"]


def test_an_unknown_stage_or_outcome_is_rejected(conn):
    pid = _posting(conn)
    with pytest.raises(ValueError):
        api.save_application(conn, pid, {"stage": "hired-ish"})
    with pytest.raises(ValueError):
        api.save_application(conn, pid, {"outcome": "ghosted"})


def test_the_list_joins_the_advert_and_the_stats_count_it(conn):
    a = _posting(conn, title="Backend Engineer")
    b = _posting(conn, title="Platform Engineer")
    c = _posting(conn, title="Data Engineer")
    api.save_application(conn, a, {"applied_at": "2026-10-01", "stage": "interview"})
    api.save_application(conn, b, {"applied_at": "2026-10-05", "outcome": "rejected"})
    api.save_application(conn, c, {"applied_at": "2026-10-10", "follow_up_at": "2026-10-09"})

    rows = api.list_applications(conn)
    assert [r["title"] for r in rows] == ["Data Engineer", "Platform Engineer",
                                          "Backend Engineer"]
    assert "60k" in rows[0]["salary_text"], rows[0]["salary_text"]

    assert len(api.list_applications(conn, open_only=True)) == 2

    stats = api.application_stats(conn)
    assert stats["total"] == 3 and stats["open"] == 2
    assert stats["rejected"] == 1
    assert stats["due"] >= 1, "the follow-up date is in the past"
    assert stats["replied"] == 2, "a stage move and an outcome both count as a reply"


def test_the_median_reply_compares_a_date_with_a_timestamp(conn):
    """A reply event carries an offset, applied_at is a bare date.

    Comparing them directly is a TypeError, and the only path that triggers it
    is the one with a real reply -- which is the whole point of the statistic.
    """
    pid = _posting(conn)
    api.save_application(conn, pid, {"applied_at": "2026-10-01"})
    api.save_application(conn, pid, {"stage": "screening"})
    stats = api.application_stats(conn)
    assert stats["median_reply_days"] is not None
    assert stats["median_reply_days"] >= 0


def test_deleting_an_application_removes_its_history_and_frees_the_posting(conn):
    pid = _posting(conn)
    api.save_application(conn, pid, {"stage": "interview"})
    assert api.delete_application(conn, pid)["ok"] is True
    assert api.get_application(conn, pid) is None
    assert conn.execute("SELECT count(*) FROM application_events").fetchone()[0] == 0
    assert dbm.get_posting(conn, pid)["status"] == "new"


def test_the_grid_gets_one_small_summary_per_row(conn):
    a = _posting(conn, title="A")
    b = _posting(conn, title="B")
    api.save_application(conn, a, {"stage": "offer", "applied_at": "2026-10-02"})
    mapped = api.applications_map(conn, [a, b])
    assert mapped[a]["stage"] == "offer"
    assert mapped[a]["applied_at"] == "2026-10-02"
    assert b not in mapped
