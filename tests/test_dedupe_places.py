"""Cross-source dedupe when one board names only a country or no employer.

Reported from the archaeology profile: EURES carried the Stadt Zürich
"Bereichsleiter*in Archäologie & Denkmalpflege" with the location "Switzerland"
while jobs.ch carried the same advert with "Zürich", and EURES carried the
Stuttgart "Referentin/Referent Prähistorische Archäologie" while LinkedIn carried
it with no company at all. Both pairs stayed on the grid.
"""
from __future__ import annotations

from jobscout import db as dbm
from jobscout.models import Posting
from jobscout.score import score_posting

PREFS = {"title_terms": ["arch"], "min_score": 0}
BODY = ("Bereichsleiterin Archäologie und Denkmalpflege: Sie tragen die "
        "Gesamtverantwortung für fünf Fachbereiche, führen das Kaderteam, "
        "entwickeln Konzepte und vertreten den Bereich nach aussen. " * 8)


def _store(conn, *, source, url, title, company="Stadt Zürich – Amt für Städtebau",
           location="Zürich", description=BODY):
    run = dbm.start_run(conn)
    p = Posting(source=source, title=title, company=company, location=location,
                url=url, external_id=url, description=description)
    pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    return pid


def test_a_country_only_location_is_not_a_different_job(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    a = _store(conn, source="jobs_ch", url="https://jobs.ch/1",
               title="Bereichsleiter*in Archäologie & Denkmalpflege",
               location="Zürich")
    b = _store(conn, source="eures", url="https://eures/1",
               title="Bereichsleiter*in Archäologie & Denkmalpflege",
               location="Switzerland")
    assert dbm.dedupe_cross_source(conn) == 1
    rows = {r["id"]: r["dup_of"] for r in conn.execute("SELECT id, dup_of FROM postings")}
    assert sum(1 for v in rows.values() if v is None) == 1
    assert {a, b} & {k for k, v in rows.items() if v is None}


def test_two_different_cities_never_merge_on_the_country(tmp_path):
    """Bremen and Munich are both in Germany, and are not the same job."""
    conn = dbm.connect(tmp_path / "t.db")
    _store(conn, source="jobs_ch", url="https://a/1",
           title="Bereichsleiter*in Archäologie & Denkmalpflege", location="Bremen")
    _store(conn, source="eures", url="https://a/2",
           title="Bereichsleiter*in Archäologie & Denkmalpflege", location="München")
    assert dbm.dedupe_cross_source(conn) == 0


def test_a_missing_employer_is_merged_on_the_body(tmp_path):
    """LinkedIn often stores no company; the advert text is then the evidence."""
    conn = dbm.connect(tmp_path / "t.db")
    a = _store(conn, source="linkedin", url="https://in/1",
               title="Referentin/Referent Prähistorische Archäologie (w/m/d), Kz. 26159",
               company="", location="Wendlingen am Neckar")
    b = _store(conn, source="eures", url="https://eures/2",
               title="Referentin/Referent Prähistorische Archäologie (w/m/d), Kz. 26159 "
                     "(Archäologe/Archäologin)",
               company="Regierungspräsidium Stuttgart Personalreferat",
               location="Germany")
    assert dbm.dedupe_missing_employer(conn) == 1
    rows = {r["id"]: r["dup_of"] for r in conn.execute("SELECT id, dup_of FROM postings")}
    # the copy that has an employer is the one that survives
    assert rows[b] is None and rows[a] == b


def test_a_missing_employer_with_a_different_body_stays(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    _store(conn, source="linkedin", url="https://in/1",
           title="Referentin/Referent Prähistorische Archäologie (w/m/d), Kz. 26159",
           company="", location="Wendlingen am Neckar")
    _store(conn, source="eures", url="https://eures/2",
           title="Referentin/Referent Prähistorische Archäologie (w/m/d), Kz. 26159 "
                 "(Archäologe/Archäologin)",
           company="Regierungspräsidium Stuttgart Personalreferat",
           location="Germany",
           description="Eine ganz andere Stelle: Grabungstechnik, Vermessung, "
                       "Fundbearbeitung, Magazinverwaltung. " * 8)
    assert dbm.dedupe_missing_employer(conn) == 0


def test_purging_a_keeper_promotes_its_copy(tmp_path):
    """A dead keeper used to leave its copies pointing at nothing, hidden for
    good even when that board still had the advert."""
    conn = dbm.connect(tmp_path / "t.db")
    keeper = _store(conn, source="jobs_ch", url="https://a/1",
                    title="Bereichsleiter*in Archäologie & Denkmalpflege")
    copy = _store(conn, source="eures", url="https://a/2",
                  title="Bereichsleiter*in Archäologie & Denkmalpflege")
    conn.execute("UPDATE postings SET dup_of=? WHERE id=?", (keeper, copy))
    conn.execute("UPDATE postings SET verify_status='dead' WHERE id=?", (keeper,))
    conn.commit()
    dbm.purge_dead(conn)
    row = conn.execute("SELECT dup_of FROM postings WHERE id=?", (copy,)).fetchone()
    assert row is not None and row["dup_of"] is None
