import json

from jobscout import db as dbm
from jobscout.models import Posting
from jobscout.score import prefs_hash, score_posting

PREFS = {"title_terms": ["entwickler"], "title_boost": 3, "keywords": [], "exclude_terms": []}


def _conn(tmp_path):
    return dbm.connect(tmp_path / "t.db")


def _p(**kw):
    base = dict(source="stepstone", title="Softwareentwickler", company="ACME GmbH",
                location="Berlin", url="https://x/1?utm=1", external_id="1")
    base.update(kw)
    return Posting(**base)


def test_fingerprint_stable_and_url_normalized():
    assert dbm.fingerprint(_p(url="https://x/1?utm=2")) == dbm.fingerprint(_p(url="https://x/1"))
    assert dbm.fingerprint(_p()) != dbm.fingerprint(_p(external_id="2"))


def test_fingerprint_ignores_mutable_fields():
    # A parser fix may change company/title/location; the posting identity must
    # not change with it, otherwise a re-scan re-inserts rows instead of refreshing.
    assert dbm.fingerprint(_p(company="Junk Senior Engineer")) == dbm.fingerprint(_p(company=""))
    assert dbm.fingerprint(_p(title="Old title")) == dbm.fingerprint(_p(title="New title"))


def test_upsert_new_then_seen(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    p = _p()
    sc = score_posting(PREFS, p)
    pid, is_new, dup = dbm.upsert(conn, p, sc, run, "t1")
    assert is_new and dup is None and sc.score == 3
    pid2, is_new2, _ = dbm.upsert(conn, p, sc, run, "t2")
    assert pid2 == pid and not is_new2
    row = dbm.get_posting(conn, pid)
    assert row["last_seen_at"] == "t2"
    assert row["score"] == 3


def test_cross_source_duplicate_is_flagged(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    p1 = _p(source="stepstone")
    p2 = _p(source="linkedin", url="https://li/9", external_id="9")
    i1, _, _ = dbm.upsert(conn, p1, score_posting(PREFS, p1), run, "t")
    i2, is_new, dup = dbm.upsert(conn, p2, score_posting(PREFS, p2), run, "t")
    assert is_new and dup == i1
    assert i2 != i1
    listed = dbm.list_postings(conn)
    assert [r["id"] for r in listed] == [i1]  # dup excluded from lists


def test_same_source_duplicate_is_flagged(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    p1 = _p(external_id="1", url="https://a/1")
    p2 = _p(external_id="2", url="https://a/2")  # same company/title/location, new id
    i1, _, _ = dbm.upsert(conn, p1, score_posting(PREFS, p1), run, "t")
    _, is_new, dup = dbm.upsert(conn, p2, score_posting(PREFS, p2), run, "t")
    assert is_new and dup == i1


def test_status_description_and_run_stats(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    p = _p()
    pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    dbm.set_status(conn, pid, "saved")
    dbm.set_description(conn, pid, "full text")
    assert dbm.get_posting(conn, pid)["status"] == "saved"
    assert dbm.get_posting(conn, pid)["description"] == "full text"
    dbm.finish_run(conn, run, {"sources": {"stepstone": {"new": 1}}})
    latest = dbm.get_run(conn)
    assert latest["id"] == run and latest["finished_at"]


def test_list_filters(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    lo = _p(title="Junior", url="https://x/lo", external_id="lo")
    hi = _p(title="Senior Softwareentwickler", url="https://x/hi", external_id="hi")
    dbm.upsert(conn, lo, score_posting(PREFS, lo), run, "t")
    dbm.upsert(conn, hi, score_posting(PREFS, hi), run, "t")
    assert [r["title"] for r in dbm.list_postings(conn, min_score=1)] == ["Senior Softwareentwickler"]
    assert len(dbm.list_postings(conn, min_score=0)) == 2
    assert len(dbm.list_postings(conn, run_id=run, min_score=0)) == 2
    assert len(dbm.list_postings(conn, run_id=999, min_score=0)) == 0


def test_counts(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    for i, src in enumerate(["a", "a", "b"]):
        p = _p(source=src, external_id=str(i), url=f"https://x/{i}")
        dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    assert dbm.counts(conn) == {"a": 2, "b": 1}


def test_rescore_all(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    p = _p()
    dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    assert dbm.get_posting(conn, 1)["score"] == 3
    total, changed = dbm.rescore_all(conn, {})   # no terms -> score 0
    assert (total, changed) == (1, 1)
    assert dbm.get_posting(conn, 1)["score"] == 0
    _, changed2 = dbm.rescore_all(conn, {})
    assert changed2 == 0


def test_list_only_filtered(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    prefs = dict(PREFS, exclude_terms=["werkstudent"])
    ok = _p(title="Softwareentwickler", external_id="ok", url="https://x/ok")
    bad = _p(title="Werkstudent Entwicklung", external_id="bad", url="https://x/bad")
    dbm.upsert(conn, ok, score_posting(prefs, ok), run, "t")
    dbm.upsert(conn, bad, score_posting(prefs, bad), run, "t")
    rows = dbm.list_postings(conn, only_filtered=True, exclude_filtered=False)
    assert [r["title"] for r in rows] == ["Werkstudent Entwicklung"]
    assert rows[0]["filtered_reason"] == "excluded:werkstudent"
    assert len(dbm.list_postings(conn)) == 1  # normal list still hides it


def test_maybe_rescore(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    p = _p()
    dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    # a legacy run without a stored hash counts as changed (one-time migration)
    out = dbm.maybe_rescore(conn, {}, {"id": run, "stats": "{}"})
    assert out is not None and out[0] == 1 and out[1] == 1
    # same preferences -> nothing to do
    same = {"id": run, "stats": json.dumps({"prefs_hash": prefs_hash({})})}
    assert dbm.maybe_rescore(conn, {}, same) is None
    # different preferences -> rescore runs again
    changed = {"id": run, "stats": json.dumps({"prefs_hash": "deadbeef"})}
    assert dbm.maybe_rescore(conn, {"min_score": 9}, changed) is not None


def test_count_matches(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    lo = _p(title="Junior", url="https://x/lo", external_id="lo")
    hi = _p(title="Senior Softwareentwickler", url="https://x/hi", external_id="hi")
    dbm.upsert(conn, lo, score_posting(PREFS, lo), run, "t")
    dbm.upsert(conn, hi, score_posting(PREFS, hi), run, "t")
    assert dbm.count_matches(conn, 1, 8) == (1, 0)
    assert dbm.count_matches(conn, 0, 3) == (2, 1)
    dbm.set_status(conn, 2, "dismissed")  # dismiss the strong one
    assert dbm.count_matches(conn, 0, 3) == (1, 0)


def test_score_histogram(tmp_path):
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    lo = _p(title="Junior", url="https://x/lo", external_id="lo")
    hi = _p(title="Senior Softwareentwickler", url="https://x/hi", external_id="hi")
    dbm.upsert(conn, lo, score_posting(PREFS, lo), run, "t")
    dbm.upsert(conn, hi, score_posting(PREFS, hi), run, "t")
    assert dict(dbm.score_histogram(conn)) == {0: 1, 3: 1}


def test_migration_adds_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(
        "CREATE TABLE postings (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "fingerprint TEXT NOT NULL UNIQUE, alt_key TEXT NOT NULL DEFAULT '', "
        "source TEXT NOT NULL DEFAULT '', "
        "company TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', "
        "location TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', "
        "external_id TEXT NOT NULL DEFAULT '', posted_at TEXT NOT NULL DEFAULT '', "
        "first_seen_at TEXT NOT NULL DEFAULT '', first_seen_run INTEGER NOT NULL DEFAULT 0, "
        "last_seen_at TEXT NOT NULL DEFAULT '', last_seen_run INTEGER NOT NULL DEFAULT 0, "
        "description TEXT NOT NULL DEFAULT '', compensation TEXT NOT NULL DEFAULT '', "
        "score REAL NOT NULL DEFAULT 0, reasons TEXT NOT NULL DEFAULT '[]', "
        "filtered INTEGER NOT NULL DEFAULT 0, filtered_reason TEXT NOT NULL DEFAULT '', "
        "dup_of INTEGER, status TEXT NOT NULL DEFAULT 'new');")
    raw.commit()
    raw.close()
    conn = dbm.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(postings)")}
    assert {"salary_min", "salary_max", "salary_annual_max", "verify_status", "verified_at"} <= cols


def test_upsert_stores_salary_and_verify_helpers(tmp_path):
    from jobscout import salary as salmod
    conn = _conn(tmp_path)
    run = dbm.start_run(conn)
    p = _p(description="Salary: €60,000 - €80,000 per year")
    salmod.fill(p)
    pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    row = dbm.get_posting(conn, pid)
    assert row["salary_currency"] == "EUR"
    assert row["salary_max"] == 80000 and row["salary_annual_max"] == 80000

    dbm.set_status(conn, pid, "saved")
    rows = dbm.postings_to_verify(conn, max_age_days=0, limit=5)
    assert [r["id"] for r in rows] == [pid]
    dbm.update_verify(conn, pid, "dead", "HTTP 404")
    assert dbm.get_posting(conn, pid)["verify_status"] == "dead"
    assert dbm.postings_to_verify(conn, max_age_days=3, limit=5) == []


# --- refreshing a description on re-scan ------------------------------------
#
# The rule exists to protect a FETCHED full description from being overwritten
# by a listing teaser. But line structure is information a length test cannot
# see, and the line-preserving extractor emits the same length or slightly
# fewer characters for identical content -- so under a bare length test the
# 3,044 collapsed arbeitnow rows would never have been repaired by a re-scan.

def test_a_longer_fresh_description_wins():
    assert dbm._prefer_fresh("much longer text here", "short")


def test_structured_text_wins_when_the_words_are_identical():
    collapsed = "We build things. You will help us build them."
    structured = "We build things.\nYou will help us build them."
    assert dbm._prefer_fresh(structured, collapsed)


def test_a_structured_copy_that_is_not_longer_still_wins():
    """The re-scan case: same words, no extra characters, more structure. A
    length test cannot see it, which is why the rule is not one."""
    collapsed = "Intro Some. The body text that goes on."
    structured = "Intro Some.\nThe body text that goes on."
    assert len(structured) <= len(collapsed), "not longer, so a length test skips it"
    assert dbm._same_words(structured, collapsed)
    assert dbm._prefer_fresh(structured, collapsed)


def test_a_shorter_different_text_does_not_win():
    """The protection this rule has always provided: a fetched full advert must
    not be replaced by a listing teaser that happens to parse differently."""
    stored_full = "A very long fetched advert. " * 40
    fresh_teaser = "Short teaser from the listing."
    assert not dbm._prefer_fresh(fresh_teaser, stored_full)


def test_an_empty_fresh_parse_never_wins():
    assert not dbm._prefer_fresh("", "anything at all")
    assert not dbm._prefer_fresh(None, "anything at all")


def test_identical_text_is_not_rewritten():
    same = "The same words.\nAcross two lines."
    assert not dbm._prefer_fresh(same, same), "nothing to gain, so leave it"


def test_collapsed_versus_structured_counts_as_the_same_words():
    a = "One. Two. Three."
    b = "One.\nTwo.\nThree."
    assert dbm._same_words(a, b)
    assert not dbm._same_words(a, "One. Two. Four.")
