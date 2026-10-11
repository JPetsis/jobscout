import json

from jobscout import api, db as dbm
from jobscout.models import Posting
from jobscout.score import score_posting

PREFS = {"title_terms": ["entwickler", "engineer"], "title_boost": 3,
         "stack_terms": ["php"], "stack_boost": 2, "stack_cap": 6,
         "keywords": [], "exclude_terms": [], "title_deny": ["vertrieb"]}


def _conn(tmp_path):
    return dbm.connect(tmp_path / "t.db")


def _p(**kw):
    base = dict(source="stepstone", title="Softwareentwickler",
                company="ACME GmbH", location="Berlin",
                url="https://x/1", external_id="1")
    base.update(kw)
    return Posting(**base)


def _add(conn, prefs=PREFS, **kw):
    run = dbm.start_run(conn)
    p = _p(**kw)
    sc = score_posting(prefs, p)
    pid, _, _ = dbm.upsert(conn, p, sc, run, "t1")
    return pid


# --- posting_dict ----------------------------------------------------------

def test_posting_dict_parses_reasons():
    row = {"id": 1, "title": "PHP Dev", "score": 15.0,
           "reasons": '[["want:php", 3.0], ["country:DE", 2.0]]'}
    out = api.posting_dict(row)
    assert out["reasons"] == [["want:php", 3.0], ["country:DE", 2.0]]
    assert out["score"] == 15.0


def test_posting_dict_tolerates_missing_or_broken_reasons():
    # A hand-edited or partially-migrated DB must not break the whole view.
    assert api.posting_dict({"id": 1, "reasons": ""})["reasons"] == []
    assert api.posting_dict({"id": 1, "reasons": "not json"})["reasons"] == []
    assert api.posting_dict({"id": 1, "reasons": '{"a": 1}'})["reasons"] == []
    assert api.posting_dict({"id": 1})["reasons"] == []


def test_posting_dict_is_json_serialisable(tmp_path):
    conn = _conn(tmp_path)
    pid = _add(conn)
    out = api.posting_dict(dbm.get_posting(conn, pid))
    json.dumps(out)                      # must not raise


# --- list_matches ----------------------------------------------------------

def test_list_matches_returns_best_first(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, company="A", url="https://x/a", external_id="a")
    _add(conn, company="B", url="https://x/b", external_id="b",
         description="php php php")
    titles = [r["title"] for r in api.list_matches(conn)]
    scores = [r["score"] for r in api.list_matches(conn)]
    assert scores == sorted(scores, reverse=True)
    assert len(titles) == 2


def test_list_matches_excludes_filtered_rows(tmp_path):
    # "vertrieb" is in title_deny, so the rule engine drops it. The window count
    # must match the digest count, so it must not appear here. Note the deny list
    # is whole-word: "Vertriebler" would NOT match, "Vertrieb Manager" does.
    conn = _conn(tmp_path)
    _add(conn, company="A", url="https://x/a", external_id="a")
    _add(conn, company="B", url="https://x/b", external_id="b",
         title="Vertrieb Manager")
    got = api.list_matches(conn)
    assert len(got) == 1
    assert got[0]["company"] == "A"


def test_list_matches_filters_by_source_status_and_score(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, company="A", url="https://x/a", external_id="a")
    _add(conn, company="B", url="https://x/b", external_id="b", source="xing")
    assert len(api.list_matches(conn, source="xing")) == 1
    assert api.list_matches(conn, source="xing")[0]["company"] == "B"
    assert len(api.list_matches(conn, min_score=99)) == 0
    assert len(api.list_matches(conn, limit=1)) == 1


def test_list_matches_respects_status(tmp_path):
    conn = _conn(tmp_path)
    pid = _add(conn)
    api.mark_posting(conn, pid, "dismissed")
    assert api.list_matches(conn, status="new") == []
    assert len(api.list_matches(conn, status="dismissed")) == 1


# --- mark_posting ----------------------------------------------------------

def test_mark_posting_writes_status(tmp_path):
    conn = _conn(tmp_path)
    pid = _add(conn)
    api.mark_posting(conn, pid, "saved")
    assert dbm.get_posting(conn, pid)["status"] == "saved"


def test_mark_posting_rejects_unknown_status(tmp_path):
    # A GUI can send an arbitrary string; validate rather than trust it.
    conn = _conn(tmp_path)
    pid = _add(conn)
    for bad in ("", "SAVED", "deleted", "new'; DROP TABLE postings--"):
        try:
            api.mark_posting(conn, pid, bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")
    assert dbm.get_posting(conn, pid)["status"] == "new"


def test_mark_posting_accepts_every_documented_status(tmp_path):
    conn = _conn(tmp_path)
    pid = _add(conn)
    for s in api.VALID_STATUSES:
        assert api.mark_posting(conn, pid, s)["status"] == s


# --- get_posting / source_counts / stats ----------------------------------

def test_get_posting_missing_returns_none(tmp_path):
    assert api.get_posting(_conn(tmp_path), 99999) is None


def test_source_counts_lists_sources(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, company="A", url="https://x/a", external_id="a")
    _add(conn, company="B", url="https://x/b", external_id="b", source="xing")
    got = {r["source"]: r["count"] for r in api.source_counts(conn)}
    assert got == {"stepstone": 1, "xing": 1}


def test_stats_shape(tmp_path):
    conn = _conn(tmp_path)
    _add(conn)
    st = api.stats(conn, {"min_score": 6, "strong_threshold": 10})
    assert set(st) >= {"matches", "strong", "min_score", "strong_threshold",
                       "histogram", "run"}
    assert isinstance(st["matches"], int)
    assert isinstance(st["histogram"], list)
    assert st["run"]["id"] >= 1
