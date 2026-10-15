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
         "permanent_terms": ["unbefristet"], "permanent_boost": 4, "permanent_cap": 4}


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
                                      {"left": 1}, 10, None)
    assert out == 10                            # detail revealed the permanence
    row = conn.execute("SELECT score, description, detail_fetched_at "
                       "FROM postings WHERE id=?", (pid,)).fetchone()
    assert row["score"] == 10 and row["description"].startswith("Die Stelle")
    assert row["detail_fetched_at"]
    # the once-only gate: further probes never re-fetch
    sc2 = score_posting(PREFS, dbm.row_to_posting(
        conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()))
    assert scanner._maybe_probe_detail(conn, cfg, mod, p, pid, sc2, fetcher,
                                       {"left": 1}, 14, None) is None


def test_probe_skips_out_of_window_budget_dups_and_visible_permanence(tmp_path):
    conn, p, sc, pid, mod, cfg, fetcher = _env(tmp_path, "unbefristet")
    assert scanner._maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher,
                                       {"left": 0}, 10, None) is None       # no budget
    assert scanner._maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher,
                                       {"left": 1}, 20, None) is None       # far gap
    assert scanner._maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher,
                                       {"left": 1}, 10, 7) is None          # dup row
    p2 = Posting(source="fake2", title="Softwareentwickler", external_id="2",
                 description="Die Stelle ist unbefristet.")
    sc_vis = score_posting(PREFS, p2)
    assert any(l.startswith("perm:") for l, _ in sc_vis.reasons)
    assert scanner._maybe_probe_detail(conn, cfg, mod, p2, pid, sc_vis, fetcher,
                                       {"left": 1}, 14, None) is None       # already visible
