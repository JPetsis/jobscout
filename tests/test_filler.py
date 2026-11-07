"""The fill queue: what it picks, how it paces, what it does with a refusal."""
from __future__ import annotations

from jobscout import db as dbm, filler
from jobscout.models import Posting
from jobscout.score import score_posting

PREFS = {"title_terms": ["developer"], "title_deny": [], "country_allow": [],
         "language_allow": [], "min_score": 6}


def _store(conn, title, description="", source="jobup", dead=False):
    p = Posting(source=source, title=title, company="A", location="Berlin",
                url=f"https://{source}.example/{title}", external_id=title,
                description=description)
    sc = score_posting(PREFS, p)
    run = dbm.start_run(conn)
    pid, _, _ = dbm.upsert(conn, p, sc, run, "2026-11-06T00:00:00+00:00", PREFS)
    if dead:
        conn.execute("UPDATE postings SET verify_status='dead' WHERE id=?", (pid,))
        conn.commit()
    return pid


def test_the_queue_takes_what_needs_text_and_skips_the_dead(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    _store(conn, "Developer one")
    _store(conn, "Developer two", description="x" * 400)
    _store(conn, "Developer three", dead=True)
    rows = filler.queue(conn)
    assert [r["title"] for r in rows] == ["Developer one"]


def test_a_refusal_halves_the_pace_and_a_second_parks_the_host():
    p = filler.Pacer(interval=1.0, clock=lambda: 0.0)
    p.refused("a.example")
    assert p.interval["a.example"] == 2.0
    p.refused("a.example")
    assert "a.example" in p.parked
    assert p.wait("a.example") is False


def test_a_filled_body_is_rescored_and_stamped(tmp_path, monkeypatch):
    conn = dbm.connect(tmp_path / "t.db")
    pid = _store(conn, "Developer")
    from jobscout.sources import jobup

    body = ("Wir suchen einen Developer. " + "Laravel und PHP. " * 30)
    monkeypatch.setattr(jobup, "fetch_detail", lambda p, f: body, raising=False)

    class F:
        errors = []

    out = filler.run(type("C", (), {"prefs": PREFS})(), conn, F(), limit=5,
                     pacer=filler.Pacer(interval=0.0))
    assert out["filled"] == 1
    row = conn.execute("SELECT description, detail_fetched_at, score "
                       "FROM postings WHERE id=?", (pid,)).fetchone()
    assert "Laravel" in row["description"]
    assert row["detail_fetched_at"]
    assert row["score"] != 0
