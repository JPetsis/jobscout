"""End-to-end: fixture postings -> score -> sqlite -> digest."""
import re

from jobscout import db as dbm
from jobscout import digest as dg
from jobscout.config import load_config
from jobscout.score import score_posting
from jobscout.sources import jobs_ch, stepstone, xing


def test_scan_to_digest(ws, fixture_text, fixture_json, strip_styles):
    cfg = load_config()
    cfg.prefs["max_age_days"] = 3650  # fixtures are frozen in time
    conn = dbm.connect(cfg.root / "jobscout.db")
    run = dbm.start_run(conn)

    postings = []
    postings += stepstone.parse(strip_styles(fixture_text("stepstone_list.html")))
    postings += xing.parse(strip_styles(fixture_text("xing_list.html")))
    postings += jobs_ch.parse(fixture_json("jobs_ch_search.json"))
    assert len(postings) == 6

    now = "2026-09-17T00:00:00+00:00"
    per_source = {}
    for p in postings:
        sc = score_posting(cfg.prefs, p)
        dbm.upsert(conn, p, sc, run, now)
        per_source.setdefault(p.source, 0)
        per_source[p.source] += 1
    dbm.finish_run(conn, run, {
        "sources": {name: {"fetched": n, "new": n} for name, n in per_source.items()},
        "requests": 4, "errors": [],
    })

    path, summary = dg.write_digest(cfg, conn)
    assert path.exists()
    text = path.read_text(encoding="utf-8")

    assert summary["new"] == 6
    assert "Softwareentwickler KI & Automatisierung" in text      # stepstone match
    assert "Softwareentwickler HMI" in text                        # jobs.ch match
    assert "Duales Studium" in text                                # xing, filtered out
    assert "Filtered out" in text
    assert "title_deny:duales studium" in text
    assert "jobscout digest --all" in text  # pointer to the all-matches view

    # digest file name uses the run date
    assert re.match(r"\d{4}-\d{2}-\d{2}-digest\.md", path.name)

    # re-running the digest renders the same run
    path2, summary2 = dg.write_digest(cfg, conn, run_id=run)
    assert summary2["run_id"] == summary["run_id"]


def test_rescore_updates_rows(ws, fixture_text, strip_styles):
    cfg = load_config()
    cfg.prefs["max_age_days"] = 3650
    conn = dbm.connect(cfg.root / "jobscout.db")
    run = dbm.start_run(conn)
    postings = stepstone.parse(strip_styles(fixture_text("stepstone_list.html")))
    now = "2026-09-17T00:00:00+00:00"
    for p in postings:
        dbm.upsert(conn, p, score_posting(cfg.prefs, p), run, now)
    cfg.prefs["title_terms"] = []
    cfg.prefs["keywords"] = []
    cfg.prefs["locations_allow"] = []
    changed = 0
    for row in dbm.all_postings(conn):
        from jobscout.models import Posting
        p = Posting(source=row["source"], title=row["title"], company=row["company"],
                    location=row["location"], url=row["url"], external_id=row["external_id"],
                    posted_at=row["posted_at"], description=row["description"])
        sc = score_posting(cfg.prefs, p)
        if sc.score != row["score"]:
            dbm.set_score(conn, row["id"], sc)
            changed += 1
    assert changed == 2
    assert all(r["score"] == 0 for r in dbm.all_postings(conn))


def test_matches_digest_covers_older_runs(ws, fixture_text, strip_styles):
    cfg = load_config()
    cfg.prefs["max_age_days"] = 3650
    conn = dbm.connect(cfg.root / "jobscout.db")
    run1 = dbm.start_run(conn)
    postings = stepstone.parse(strip_styles(fixture_text("stepstone_list.html")))
    now = "2026-09-17T00:00:00+00:00"
    for p in postings:
        dbm.upsert(conn, p, score_posting(cfg.prefs, p), run1, now)
    dbm.finish_run(conn, run1, {"sources": {}, "requests": 0, "errors": []})
    run2 = dbm.start_run(conn)  # a later run that found nothing new
    dbm.finish_run(conn, run2, {"sources": {}, "requests": 0, "errors": []})

    path, summary = dg.write_matches_digest(cfg, conn)
    text = path.read_text(encoding="utf-8")
    assert path.name.endswith("-matches.md")
    assert summary["matches"] >= 1
    assert "Softwareentwickler KI" in text  # from run 1, still listed

    path2, _ = dg.write_matches_digest(cfg, conn, top=1)
    assert "(1 of 2)" in path2.read_text(encoding="utf-8")  # capped heading
