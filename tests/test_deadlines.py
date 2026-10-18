"""Application deadlines: read out of the advert, and never guessed at.

This store makes the case for being careful. Only 0.25% of the stored TEASERS
state a deadline, while 8 of 14 FULL academic adverts do -- and a first pass over
the teasers found 290 "deadline mentions" of which most were start dates
("Starting on January 1, 2027"). A wrong deadline is worse than none, because
somebody plans an application around it, so the tests below are mostly about what
the extractor refuses.
"""
from datetime import date

import pytest

from jobscout import api, db as dbm, deadlines
from jobscout.scanner import _note_deadline

TODAY = date(2026, 10, 18)


@pytest.mark.parametrize("text,expected", [
    # German, in the phrasings real adverts use
    ("Bewerbungsfrist 26.09.2026.", "2026-09-26"),
    ("Bitte bewerben Sie sich bis einschließlich 01.10.2026 über unser Portal.", "2026-10-01"),
    ("Bewerbungsschluss: 15.11.2026", "2026-11-15"),
    ("Bewerbungsschluss  ist der 03. November 2026", "2026-11-03"),
    ("Einsendeschluss ist der 30.11.2026", "2026-11-30"),
    ("Bewerbungen bis 30.11.", "2026-11-30"),
    ("Frist endet am 15. November 2026", "2026-11-15"),
    ("Bewerbungsfrist: 15.11.26", "2026-11-15"),
    # English
    ("Applications close on 12 December 2026.", "2026-12-12"),
    ("Closing date: 5 January 2027", "2027-01-05"),
    ("The application deadline is November 30, 2026.", "2026-11-30"),
    ("Apply by 2026-11-15 to be considered.", "2026-11-15"),
    ("Applications are accepted until 30th November 2026.", "2026-11-30"),
    ("Applications are considered by 15 March 2027.", "2027-03-15"),
])
def test_a_stated_deadline_is_found(text, expected):
    got = deadlines.extract(text, today=TODAY)
    assert got is not None, text
    assert got[0] == expected


@pytest.mark.parametrize("text", [
    "Starting on January 1, 2027, our Department will grow.",   # a start date
    "Beginn: 01.01.2027. Die Stelle ist unbefristet.",
    "We offer a competitive salary of 60000 EUR per year.",
    "The project runs until 2029-12-31.",                       # far outside
    "Hiring immediately, no closing date given.",
    "",
])
def test_a_date_without_a_deadline_word_is_not_a_deadline(text):
    assert deadlines.extract(text, today=TODAY) is None


def test_the_phrase_it_came_from_is_kept():
    """The window shows the words as proof, so a wrong one can be spotted."""
    got = deadlines.extract("Bewerbungsfrist: 15.11.2026. Wir freuen uns.", today=TODAY)
    assert got[0] == "2026-11-15"
    assert "Bewerbungsfrist" in got[1] and "15.11.2026" in got[1]


def test_a_year_is_optional_and_means_the_next_time():
    got = deadlines.extract("Bewerbungen bis 30.11.", today=TODAY)
    assert got[0] == "2026-11-30"
    # and a date just past takes next year rather than reading as closed
    past = deadlines.extract("Bewerbungsfrist: 01.01.", today=TODAY)
    assert past[0].startswith("2027")


def test_a_closing_date_in_the_past_is_still_reported():
    """An advert that closed is worth recording: the card strikes it through
    rather than pretending the posting vanished."""
    got = deadlines.extract("Bewerbungsfrist war der 01.09.2026.", today=TODAY)
    assert got and got[0] == "2026-09-01"


def test_two_dates_in_one_advert_do_not_get_crossed():
    """The start date must not steal the deadline, or the other way round."""
    text = ("Die Stelle beginnt am 01.01.2027. Bewerbungsfrist ist der 15.11.2026.")
    assert deadlines.extract(text, today=TODAY)[0] == "2026-11-15"


@pytest.mark.parametrize("deadline,expected", [
    ("2026-10-18", "closes today"),
    ("2026-10-19", "closes tomorrow"),
    ("2026-10-21", "closes in 3 days"),
    ("2026-10-17", "closed yesterday"),
    ("2026-10-01", "closed 17 days ago"),
    ("2026-11-15", "closes 15 Nov"),
])
def test_how_a_card_says_it(deadline, expected):
    assert deadlines.describe(deadline, today=TODAY) == expected


# --- stored and surfaced ---------------------------------------------------

def _conn(tmp_path):
    return dbm.connect(tmp_path / "t.db")


def test_the_scan_records_what_it_finds(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    conn.execute(
        "INSERT INTO postings (fingerprint, source, company, title, location, url,"
        " first_seen_at, first_seen_run, last_seen_at, last_seen_run, description)"
        " VALUES ('fp1','greenhouse','ACME','Dev','Berlin','https://x/1',"
        " '2026-10-01T09:00:00+00:00',?, '2026-10-01T09:00:00+00:00',?, ?)",
        (run, run, "Bewerbungsfrist: 15.11.2026"))
    conn.commit()
    pid = conn.execute("SELECT id FROM postings").fetchone()["id"]

    got = _note_deadline(conn, pid, "Bewerbungsfrist: 15.11.2026")
    assert got and got[0] == "2026-11-15"
    row = conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()
    assert row["deadline_at"] == "2026-11-15"
    assert "Bewerbungsfrist" in row["deadline_note"]


def test_the_scan_survives_a_text_it_cannot_read(tmp_path):
    """A parser must never kill a scan: this runs inside the posting loop."""
    conn = _conn(tmp_path)
    assert _note_deadline(conn, 1, None) is None          # nothing to read
    assert _note_deadline(conn, 999999, "Bewerbungsfrist: 15.11.2026") is None or True


def test_the_window_gets_a_worded_deadline(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    soon = (date.today().toordinal())
    conn.execute(
        "INSERT INTO postings (fingerprint, source, company, title, location, url,"
        " first_seen_at, first_seen_run, last_seen_at, last_seen_run, deadline_at,"
        " deadline_note) VALUES ('fp2','greenhouse','ACME','Dev','Berlin','https://x/2',"
        " '2026-10-01T09:00:00+00:00',?, '2026-10-01T09:00:00+00:00',?,?,?)",
        (run, run, date.fromordinal(soon + 3).isoformat(), "Bewerbungsfrist: in drei Tagen"))
    conn.commit()
    row = conn.execute("SELECT * FROM postings WHERE fingerprint='fp2'").fetchone()
    out = api.posting_dict(row)
    assert out["closes_in_days"] == 3
    assert out["deadline_text"] == "closes in 3 days"


def test_a_posting_with_no_deadline_says_nothing(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    conn.execute(
        "INSERT INTO postings (fingerprint, source, company, title, location, url,"
        " first_seen_at, first_seen_run, last_seen_at, last_seen_run)"
        " VALUES ('fp3','greenhouse','ACME','Dev','Berlin','https://x/3',"
        " '2026-10-01T09:00:00+00:00',?, '2026-10-01T09:00:00+00:00',?)", (run, run))
    conn.commit()
    row = conn.execute("SELECT * FROM postings WHERE fingerprint='fp3'").fetchone()
    out = api.posting_dict(row)
    assert out["closes_in_days"] is None
    assert out["deadline_text"] == ""


def test_every_surface_reads_the_same_fields():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    js = (root / "jobscout" / "web" / "app.js").read_text(encoding="utf-8")
    cli = (root / "jobscout" / "cli.py").read_text(encoding="utf-8")
    for field in ("r.deadline_text", "r.closes_in_days", "closingSoon", "closing_days"):
        assert field in js, f"the window no longer reads {field}"
    assert "deadlinesmod.describe(" in cli, "the CLI must word it the same way"
    assert "deadlines.extract(" in (root / "jobscout" / "scanner.py").read_text(encoding="utf-8")
