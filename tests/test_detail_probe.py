"""The near-threshold detail probe: a match that only LOOKS weak because its
contract type lives on the detail page gets completed once and rescored.

Measured motivation: the Uni-Leipzig developer job reads "Befristung
unbefristet" on its myScience detail page but not in the teaser, so at scan
time it sat below strong_threshold and the permanence boost never applied.
"""
import types

from jobscout import db as dbm
from jobscout import scanner
from jobscout.models import Posting
from jobscout.score import score_posting

PREFS = {"min_score": 6, "strong_threshold": 10,
         "title_boost": 3, "title_cap": 6, "title_terms": ["entwickler", "software"],
         "permanent_terms": ["unbefristet"], "permanent_boost": 4, "permanent_cap": 4,
         "temporary_terms": ["befristet"]}


def _env(tmp_path, detail_text):
    conn = dbm.connect(tmp_path / "db.sqlite")
    p = Posting(source="fake", title="Softwareentwickler", external_id="1",
                url="https://example.invalid/1", description="Team gesucht.")
    sc = score_posting(PREFS, p)               # 6: two title hits, capped
    run = dbm.start_run(conn)
    pid, is_new, dup = dbm.upsert(conn, p, sc, run, "2026-10-15T00:00:00+00:00")
    mod = types.SimpleNamespace(fetch_detail=lambda post, fetcher: detail_text)
    cfg = types.SimpleNamespace(prefs=PREFS)
    fetcher = types.SimpleNamespace(errors=[])
    return conn, p, sc, pid, mod, cfg, fetcher


def test_probe_promotes_a_near_threshold_row_once(tmp_path):
    conn, p, sc, pid, mod, cfg, fetcher = _env(tmp_path, "Die Stelle ist unbefristet.")
    assert sc.score == 6 and not sc.filtered
    out = scanner._maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher,
                                      {"left": 1}, None)
    assert out is not None and out.score == 10.0  # detail revealed the permanence
    row = conn.execute("SELECT score, description, detail_fetched_at "
                       "FROM postings WHERE id=?", (pid,)).fetchone()
    assert row["score"] == 10 and row["description"].startswith("Die Stelle")
    assert row["detail_fetched_at"]
    # the once-only gate: further probes never re-fetch
    sc2 = score_posting(PREFS, dbm.row_to_posting(
        conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()))
    assert scanner._maybe_probe_detail(conn, cfg, mod, p, pid, sc2, fetcher,
                                       {"left": 1}, None) is None


def test_probe_also_bumps_rows_already_over_the_threshold(tmp_path):
    """The boost must apply on its merits, not only to near-misses: a match
    that already sits at 12 gets its detail completed and climbs to 16, so a
    permanent 13+ outranks a fixed-term 12 instead of depending on where the
    contract happened to be written."""
    from jobscout.score import Score
    conn, p, sc, pid, mod, cfg, fetcher = _env(tmp_path, "Die Stelle ist unbefristet.")
    sc_strong = Score(score=12.0, reasons=[["title:software", 3.0]])
    out = scanner._maybe_probe_detail(conn, cfg, mod, p, pid, sc_strong, fetcher,
                                      {"left": 1}, None)
    # fired despite score >= strong; returns the RESCORED truth, not 12+4
    assert out is not None and out.score == 10.0


def test_rescan_does_not_demote_a_probed_row(tmp_path):
    """A later scan re-parses the TEASER only; the stored detail text must win,
    or the promoted score silently regresses (measured 16 -> 12)."""
    conn, p, sc, pid, mod, cfg, fetcher = _env(tmp_path, "Die Stelle ist unbefristet.")
    scanner._maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher, {"left": 1}, None)
    assert conn.execute("SELECT score FROM postings WHERE id=?",
                        (pid,)).fetchone()[0] == 10.0
    fresh = Posting(source="fake", title="Softwareentwickler", external_id="1",
                    url="https://example.invalid/1", description="Team gesucht.")
    sc_fresh = score_posting(PREFS, fresh)
    dbm.upsert(conn, fresh, sc_fresh, 2, "2026-10-16T00:00:00+00:00", PREFS)
    row = conn.execute("SELECT score, description FROM postings WHERE id=?",
                       (pid,)).fetchone()
    assert row["score"] == 10.0                 # not 6: scored from the STORED text
    assert row["description"].startswith("Die Stelle")


def test_probe_persists_a_reason_only_marker(tmp_path):
    """A 0-point marker (tmp:befristet) changes no score but MUST be stored and
    reported; a score-only change check silently dropped it (measured: the
    Tuebingen row got no [befristet] pill)."""
    conn, p, sc, pid, mod, cfg, fetcher = _env(tmp_path, "Die Stelle ist befristet bis 2030.")
    out = scanner._maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher,
                                      {"left": 1}, None)
    assert out is not None
    assert any(str(l).startswith("tmp:") for l, _ in out.reasons)
    stored = conn.execute("SELECT reasons FROM postings WHERE id=?",
                          (pid,)).fetchone()[0]
    assert "tmp:befristet" in stored


def test_probe_skips_budget_dups_below_min_and_visible_permanence(tmp_path):
    conn, p, sc, pid, mod, cfg, fetcher = _env(tmp_path, "unbefristet")
    assert scanner._maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher,
                                       {"left": 0}, None) is None           # no budget
    assert scanner._maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher,
                                       {"left": 1}, 7) is None              # dup row
    p_low = Posting(source="fake3", title="Software", external_id="3")
    sc_low = score_posting(PREFS, p_low)        # 3 < min_score, not displayed
    assert sc_low.score == 3
    assert scanner._maybe_probe_detail(conn, cfg, mod, p_low, pid, sc_low, fetcher,
                                       {"left": 1}, None) is None
    p2 = Posting(source="fake2", title="Softwareentwickler", external_id="2",
                 description="Die Stelle ist unbefristet.")
    sc_vis = score_posting(PREFS, p2)
    assert any(l.startswith("perm:") for l, _ in sc_vis.reasons)
    assert scanner._maybe_probe_detail(conn, cfg, mod, p2, pid, sc_vis, fetcher,
                                       {"left": 1}, None) is None           # already visible
