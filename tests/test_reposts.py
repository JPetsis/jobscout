"""One board republishing an advert: the Girona case and its traps."""
from __future__ import annotations

from jobscout import db as dbm
from jobscout.models import Posting
from jobscout.score import score_posting

PREFS = {"title_terms": ["software", "entwickler"], "min_score": 0}
BODY = ("Der Plan steht, jetzt fehlst du: Bring unsere Software von Legacy-PHP auf "
        "Symfony und React. Auf einen Blick: PHP 8.1, Symfony, MySQL, Git, CI/CD. "
        "Gehalt 52.000-62.000 EUR, unbefristet, hybrid, Berlin-Zehlendorf. " * 6)


def _store(conn, *, url, title, company="Girona Softwareentwicklung GmbH",
           location="Berlin", description=BODY):
    run = dbm.start_run(conn)
    p = Posting(source="arbeitnow", title=title, company=company, location=location,
                url=url, external_id=url, description=description)
    pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    return pid


def test_a_repost_with_a_qualifier_in_the_title_is_one_advert(tmp_path):
    """arbeitnow published this on 9 November as "… – PHP/Symfony & React" and on
    11 November as "… – Hybrid PHP/Symfony & React", same body, new slug."""
    conn = dbm.connect(tmp_path / "t.db")
    old = _store(conn, url="https://a/1",
                 title="Senior Fullstack-Entwickler (m/w/d) – PHP/Symfony & React")
    new = _store(conn, url="https://a/2",
                 title="Senior Fullstack-Entwickler (m/w/d) – Hybrid PHP/Symfony & React")
    assert dbm.dedupe_reposts(conn) == 1
    rows = {r["id"]: r["dup_of"] for r in conn.execute("SELECT id, dup_of FROM postings")}
    # the more specific title is the one left standing
    assert rows[new] is None
    assert rows[old] == new


def test_a_different_body_is_a_different_advert(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    _store(conn, url="https://a/1",
           title="Senior Fullstack-Entwickler (m/w/d) – PHP/Symfony & React")
    _store(conn, url="https://a/2",
           title="Senior Fullstack-Entwickler (m/w/d) – Hybrid PHP/Symfony & React",
           description="Ganz andere Stelle: Data Engineering, Python, Airflow. " * 20)
    assert dbm.dedupe_reposts(conn) == 0


def test_a_regional_variant_is_not_a_repost(tmp_path):
    """A shared prefix used to be enough: "… - Midwest" vanished behind the
    national advert. Different body, different job."""
    conn = dbm.connect(tmp_path / "t.db")
    _store(conn, url="https://a/1", title="Senior Account Executive",
           company="ACME", location="Berlin")
    _store(conn, url="https://a/2", title="Senior Account Executive - Midwest",
           company="ACME", location="Berlin",
           description="Territory: Midwest. Quota 1.2M USD. Travel 40%. " * 20)
    assert dbm.dedupe_reposts(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM postings WHERE dup_of IS NULL").fetchone()[0] == 2


def test_a_three_letter_employer_is_not_ignored(tmp_path):
    """n8n and B12 are real employers; a company shorter than four characters
    used to escape the same-board pass entirely."""
    conn = dbm.connect(tmp_path / "t.db")
    a = _store(conn, url="https://a/1", title="Staff Platform Engineer, Agentic Engineering",
               company="n8n", location="Berlin Office (Remote)")
    b = _store(conn, url="https://a/2", title="Staff Platform Engineer, Agentic Engineering",
               company="n8n", location="Berlin Office (Remote)")
    dbm.dedupe_same_source(conn)
    # either the insert path or the pass catches it; what matters is one row
    assert conn.execute("SELECT dup_of FROM postings WHERE id=?", (b,)).fetchone()[0] == a


def test_one_boilerplate_body_is_not_evidence_of_one_job(tmp_path):
    """vivenu advertises an Enterprise Account Executive and a BDR with the same
    body word for word. A body-only rule merged them; the title must be nested
    (or differ only by a number) as well."""
    conn = dbm.connect(tmp_path / "t.db")
    _store(conn, url="https://a/1", title="Enterprise Account Executive (d/f/m)",
           company="vivenu", location="Berlin")
    _store(conn, url="https://a/2", title="Business Development Representative (BDR)",
           company="vivenu", location="Berlin")
    assert dbm.dedupe_reposts(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM postings WHERE dup_of IS NULL").fetchone()[0] == 2
