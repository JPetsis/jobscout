"""Housekeeping: what a store keeps, what it drops, and a rescore that stays fast.

The re-score is the reason this file exists. It used to load every advert body to
decide that a row's title did not match, which made the work grow with the whole
table. The tests here pin the two-phase result to the one-phase result: the same
verdict, whatever route it took.
"""
from __future__ import annotations

from datetime import date, timedelta

from jobscout import db as dbm
from jobscout.models import Posting
from jobscout.score import score_posting

PREFS = {"title_terms": ["software", "entwickler"], "title_deny": ["sales"],
         "max_age_days": 21, "min_score": 0}


def _store(conn, *, url, title, company="ACME GmbH", location="Bremen",
           description="", posted_at="", status="new"):
    run = dbm.start_run(conn)
    p = Posting(source="stepstone", title=title, company=company,
                location=location, url=url, external_id=url,
                posted_at=posted_at, description=description)
    pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    conn.execute("UPDATE postings SET status=? WHERE id=?", (status, pid))
    conn.commit()
    return pid


def _full_score(conn, prefs=PREFS):
    out = {}
    for row in conn.execute("SELECT * FROM postings"):
        out[row["id"]] = score_posting(prefs, dbm.row_to_posting(row))
    return out


def test_rescore_reaches_the_same_verdict_as_a_full_pass(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    body = "Wir suchen eine Person für PHP, Laravel und TYPO3. " * 20
    _store(conn, url="https://x/1", title="Softwareentwickler (m/w/d)",
           description=body, posted_at=date.today().isoformat())
    _store(conn, url="https://x/2", title="Barista (m/w/d)", description=body)
    _store(conn, url="https://x/3", title="Sales Manager", description=body)
    _store(conn, url="https://x/4", title="Softwareentwickler",
           description=body, posted_at="2020-01-01")

    dbm.rescore_all(conn, PREFS)
    expected = _full_score(conn)
    for pid, want in expected.items():
        row = conn.execute("SELECT score, reasons, filtered, filtered_reason "
                           "FROM postings WHERE id=?", (pid,)).fetchone()
        assert row["score"] == want.score, pid
        assert row["filtered_reason"] == want.reason, pid
        assert bool(row["filtered"]) == bool(want.filtered), pid


def test_a_row_hidden_by_its_title_keeps_only_a_teaser(tmp_path):
    """The body of a posting that can never be shown is rent on an empty room."""
    conn = dbm.connect(tmp_path / "t.db")
    body = "Ein sehr langer Adverttext. " * 100
    hidden = _store(conn, url="https://x/1", title="Barista (m/w/d)", description=body)
    kept = _store(conn, url="https://x/2", title="Softwareentwickler", description=body)
    d_hidden = conn.execute("SELECT length(description) n FROM postings WHERE id=?",
                            (hidden,)).fetchone()["n"]
    d_kept = conn.execute("SELECT length(description) n FROM postings WHERE id=?",
                          (kept,)).fetchone()["n"]
    assert d_hidden <= dbm.TEASER_CHARS
    assert d_kept >= len(body) - 2      # storage normalises trailing whitespace


def test_stale_rows_are_deleted_but_saved_ones_survive(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    dead = _store(conn, url="https://x/1", title="Softwareentwickler")
    saved = _store(conn, url="https://x/2", title="Softwareentwickler",
                   status="saved")
    conn.execute("UPDATE postings SET filtered=1, filtered_reason='stale:2026-01-01'")
    conn.commit()
    assert dbm.purge_stale(conn) == 1
    left = {r["id"] for r in conn.execute("SELECT id FROM postings")}
    assert left == {saved}
    assert dead not in left


def test_retention_only_takes_hidden_rows_past_the_window(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    old = (date.today() - timedelta(days=60)).isoformat() + "T10:00:00+00:00"
    hidden_old = _store(conn, url="https://x/1", title="Barista", company="Alt GmbH")
    hidden_new = _store(conn, url="https://x/2", title="Barista", company="Neu GmbH")
    visible_old = _store(conn, url="https://x/3", title="Softwareentwickler")
    saved_old = _store(conn, url="https://x/4", title="Barista",
                       company="Gespeichert GmbH", status="saved")
    conn.execute("UPDATE postings SET first_seen_at=?", (old,))
    conn.execute("UPDATE postings SET first_seen_at=? WHERE id=?",
                 (date.today().isoformat() + "T10:00:00+00:00", hidden_new))
    conn.execute("UPDATE postings SET filtered=1, filtered_reason='no_title_hit' "
                 "WHERE id IN (?,?,?)", (hidden_old, hidden_new, saved_old))
    conn.execute("UPDATE postings SET filtered=0, filtered_reason='' WHERE id=?",
                 (visible_old,))
    conn.commit()

    assert dbm.purge_old_filtered(conn, 30) == 1
    left = {r["id"] for r in conn.execute("SELECT id FROM postings")}
    assert left == {hidden_new, visible_old, saved_old}


def test_retention_zero_keeps_everything(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    _store(conn, url="https://x/1", title="Barista")
    conn.execute("UPDATE postings SET filtered=1, filtered_reason='no_title_hit', "
                 "first_seen_at='2020-01-01T00:00:00+00:00'")
    conn.commit()
    assert dbm.purge_old_filtered(conn, 0) == 0
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 1


def test_maintain_reports_what_it_did(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    _store(conn, url="https://x/1", title="Barista")
    conn.execute("UPDATE postings SET filtered=1, filtered_reason='stale:2026-01-01', "
                 "first_seen_at='2020-01-01T00:00:00+00:00'")
    conn.commit()
    out = dbm.maintain(conn, {"retention_days": 30})
    assert out["stale"] == 1
    assert out["old"] == 0          # the stale purge already took it
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 0


def test_batched_term_matching_agrees_with_one_term_at_a_time():
    """The fast paths (word set, substring plus boundary check) must answer
    exactly what the per-term regex answers, or a re-score changes scores."""
    from jobscout.score import _hit, _hits
    from jobscout.textutil import norm

    terms = ["php", "laravel", "c++", "front-end", "software engineer", "java",
             "typ3", "react", "node.js", "entwickler"]
    texts = [
        norm("Wir suchen PHP-Entwickler mit Laravel, C++ und Front-End; Java ist ein Plus."),
        norm("Software Engineer (m/w/d) fuer React und Node.js, kein PHP."),
        norm("Softwareentwickler fuer TYPO3 und JavaScript, Java-Kenntnisse."),
        norm(""),
    ]
    for text in texts:
        for strict in (True, False):
            got = _hits(terms, text, strict)
            want = {i for i, t in enumerate(terms) if _hit(text, t, strict)}
            assert got == want, (text[:40], strict, got, want)
