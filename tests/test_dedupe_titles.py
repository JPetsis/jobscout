"""The same post on two boards, when the boards word the title differently."""
from __future__ import annotations

from jobscout import db as dbm
from jobscout.db import dedupe_title


def test_gender_markers_hyphens_and_city_are_not_part_of_the_title():
    assert dedupe_title("Software-Entwickler PHP (m/w/d) in Dresden", "dresden") == \
        dedupe_title("Softwareentwickler PHP (m/w/d)", "dresden")
    assert dedupe_title("Software Entwickler:in (m/w/d)", "linz") == \
        dedupe_title("Software Entwickler_in (w/m/d)", "linz")


def test_a_different_specialisation_stays_different():
    assert dedupe_title("Softwareentwickler PHP", "bremen") != \
        dedupe_title("Softwareentwickler Java", "bremen")


PREFS = {"title_terms": ["software"], "title_deny": [], "min_score": 0}


def _store(conn, *, url, source, title, company, location="Bremen"):
    from jobscout.models import Posting
    from jobscout.score import score_posting
    run = dbm.start_run(conn)
    p = Posting(source=source, title=title, company=company, location=location,
                url=url, external_id=url)
    pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    conn.commit()
    return pid


def test_one_title_at_three_employers_merges_the_pair_it_belongs_to(tmp_path):
    """Bremen has "Softwareentwickler PHP" at both endigo and Orange Engineering.
    The first employer used to be the only keeper, so the real duplicate between
    Orange's XING and Jobware rows was never reached."""
    conn = dbm.connect(tmp_path / "t.db")
    endigo = _store(conn, url="https://x/e", source="xing",
                    title="Softwareentwickler (m/w/d) PHP", company="endigo GmbH")
    xing = _store(conn, url="https://x/o1", source="xing",
                  title="Softwareentwickler PHP (m/w/d)",
                  company="Orange Engineering GmbH & Co. KG")
    jobware = _store(conn, url="https://j/o1", source="jobware",
                     title="Softwareentwickler PHP (m/w/d)",
                     company="Orange Engineering GmbH &amp; Co. KG",
                     location="Bremen (DE)")
    assert dbm.dedupe_cross_source(conn) == 1
    rows = {r["id"]: r["dup_of"] for r in
            conn.execute("SELECT id, dup_of FROM postings")}
    assert rows[jobware] == xing
    assert rows[xing] is None and rows[endigo] is None
