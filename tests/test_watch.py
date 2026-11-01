"""The two questions a job search keeps asking: what closes, and what is broken.

Both are silent failures otherwise: a board that returns nothing looks like a
board with nothing, and a deadline that passes looks like a posting that was
never interesting.
"""
from datetime import date, timedelta

from jobscout import api, db as dbm
from jobscout.config import init_workspace, load_config
from jobscout.models import Posting
from jobscout.requirements import extract
from jobscout.score import score_posting

PREFS = {"title_terms": ["engineer"], "title_boost": 3, "keywords": [],
         "exclude_terms": [], "require_title_match": False}


def _store(conn, *, title, status="new", deadline="", description=""):
    run = dbm.start_run(conn)
    p = Posting(source="test", title=title, company="ACME", url=f"https://x/{title}",
                description=description, external_id=title)
    pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    conn.execute("UPDATE postings SET status=?, deadline_at=? WHERE id=?",
                 (status, deadline, pid))
    conn.commit()
    return pid


def _cfg(tmp_path, enabled=("xing",)):
    return type("C", (), {"prefs": dict(PREFS), "enabled": list(enabled),
                          "path": tmp_path / "config.yaml"})()


def test_a_board_returning_nothing_builds_a_zero_streak(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    dbm.note_source_health(conn, "stepstone", 127, 60, when="2026-10-01T10:00:00+00:00")
    dbm.note_source_health(conn, "stepstone", 0, 0, "HTTP 200 but no cards",
                           when="2026-10-02T10:00:00+00:00")
    dbm.note_source_health(conn, "stepstone", 0, 0, when="2026-10-03T10:00:00+00:00")
    h = dbm.source_health(conn)["stepstone"]
    assert h["zero_streak"] == 2
    assert h["last_ok_fetched"] == 127, "the last real result is the yardstick"
    assert h["last_ok"] == "2026-10-01T10:00:00+00:00"
    assert h["last_error"] == "", "the last run's error, not an old one"

    dbm.note_source_health(conn, "stepstone", 40, 5, when="2026-10-04T10:00:00+00:00")
    assert dbm.source_health(conn)["stepstone"]["zero_streak"] == 0


def test_source_health_lists_enabled_boards_and_flags_zeros(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    dbm.note_source_health(conn, "xing", 0)
    rows = api.source_health(_cfg(tmp_path), conn)
    xing = [r for r in rows if r["name"] == "xing"][0]
    assert xing["label"] and xing["enabled"] is True and xing["zero_streak"] == 1
    assert rows == sorted(rows, key=lambda r: (not r["enabled"], -r["zero_streak"],
                                               r["label"].lower()))


def test_what_closes_soon_says_which_of_them_you_have_not_applied_to(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    soon = (date.today() + timedelta(days=3)).isoformat()
    far = (date.today() + timedelta(days=60)).isoformat()
    a = _store(conn, title="Engineer closing", deadline=soon)
    _store(conn, title="Engineer far", deadline=far)
    dbm.save_application(conn, a, {"applied_at": date.today().isoformat(),
                                   "stage": "applied"})

    out = api.deadline_watch(_cfg(tmp_path), conn, days=14)
    assert out["count"] == 1 and out["unapplied"] == 0
    assert out["items"][0]["days_left"] == 3
    assert out["items"][0]["applied"] is True


def test_a_word_you_dismissed_repeatedly_is_offered_as_a_reject_term(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    for i in range(4):
        _store(conn, title=f"Vertriebsleiter {i}", status="dismissed")
    _store(conn, title="Engineer kept", status="saved")
    _store(conn, title="Engineer kept two", status="applied")

    out = api.reject_suggestions(_cfg(tmp_path), conn)
    assert any(s["term"] == "vertriebsleiter" for s in out["items"])
    assert out["dismissed"] == 4 and out["needed"] == 3


def test_the_suggestion_card_is_told_why_it_is_empty(tmp_path):
    """Reported: the card never appeared. It hid itself when it had nothing to
    say, which reads as a broken feature rather than an empty one."""
    conn = dbm.connect(tmp_path / "t.db")
    _store(conn, title="Vertriebsleiter", status="dismissed")
    out = api.reject_suggestions(_cfg(tmp_path), conn)
    assert out["items"] == [] and out["dismissed"] == 1 and out["needed"] == 3


def test_adding_a_reject_term_keeps_the_file_comments(tmp_path):
    init_workspace(tmp_path)
    cfg = load_config(tmp_path)
    before = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    out = api.add_reject_term(cfg, "vertriebsleiter")
    after = (tmp_path / "config.yaml").read_text(encoding="utf-8")

    assert out["ok"] is True and "vertriebsleiter" in out["value"]
    assert "# jobscout config" in after, "the documentation survives"
    assert after.count("\n") > before.count("\n") - 5


def test_an_advert_states_what_no_filter_can_catch():
    text = ("Wir suchen eine Person mit sehr guten Deutschkenntnissen (C1). "
            "Eine Promotion ist erwünscht, mindestens 3 Jahre Erfahrung.")
    labels = {(r["kind"], r["label"]) for r in extract(text)}
    assert ("language", "German C1") in labels
    assert ("degree", "PhD / doctorate") in labels
    assert ("experience", "3+ years") in labels
    # silence is not a requirement
    assert extract("Backend developer, Laravel and Vue.") == []


def test_the_advert_requirements_reach_the_window(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    pid = _store(conn, title="Engineer", description="PhD required. Fluent German.")
    row = conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()
    out = api.posting_dict(row)
    labels = {r["label"] for r in out["requirements"]}
    assert "PhD / doctorate" in labels and "German fluent" in labels
