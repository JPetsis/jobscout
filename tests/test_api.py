import json

import pytest

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


# --- GUI thread safety -----------------------------------------------------
# pywebview runs js_api methods in SEPARATE THREADS. Without
# check_same_thread=False this raises ProgrammingError; with the flag but no
# lock, concurrent writers corrupt state. Both halves are covered here.

def test_connection_is_usable_from_another_thread(tmp_path):
    import threading as th

    conn = _conn(tmp_path)
    pid = _add(conn)
    out: dict = {}
    err: list = []

    def work():
        try:
            out["n"] = len(api.list_matches(conn))
            api.mark_posting(conn, pid, "saved")
        except Exception as e:                                # noqa: BLE001
            err.append(e)

    t = th.Thread(target=work)
    t.start()
    t.join(timeout=10)
    assert not err, f"cross-thread DB use failed: {err}"
    assert out["n"] == 1
    assert dbm.get_posting(conn, pid)["status"] == "saved"


def test_concurrent_writers_do_not_corrupt_state(tmp_path):
    import threading as th

    conn = _conn(tmp_path)
    pids = [_add(conn, company=f"C{i}", url=f"https://x/{i}", external_id=str(i))
            for i in range(8)]
    err: list = []

    def work(pid, status):
        try:
            for _ in range(25):
                api.mark_posting(conn, pid, status)
                api.list_matches(conn, limit=5)
        except Exception as e:                                # noqa: BLE001
            err.append(e)

    threads = [th.Thread(target=work, args=(p, "saved" if i % 2 else "applied"))
               for i, p in enumerate(pids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not err, f"concurrent access failed: {err[:3]}"
    # every row landed on one of the two valid values -- no torn writes
    for p in pids:
        assert dbm.get_posting(conn, p)["status"] in ("saved", "applied")


# --- what the pane says when there is no description ------------------------
#
# Reported problem this guards: clicking a XING card showed an empty area with
# no explanation. "Withdrawn", "no link" and "this board publishes no text" mean
# very different things to the reader -- one of them means stop looking -- and
# they all looked identical.

@pytest.fixture()
def ws_cfg(tmp_path, monkeypatch):
    """A real config in a throwaway workspace."""
    from jobscout.config import init_workspace, load_config
    monkeypatch.setenv("JOBSCOUT_HOME", str(tmp_path / "home"))
    init_workspace()
    return load_config()


def _no_network(monkeypatch, note, text=""):
    """Both extractors stubbed: these tests are about the plumbing, not HTTP."""
    from jobscout import detail
    monkeypatch.setattr(detail, "html_detail", lambda url, f: text or None)
    monkeypatch.setattr(detail, "html_detail_verbose",
                        lambda url, f: (text, "" if text else note))


def test_an_empty_description_is_explained(tmp_path, ws_cfg, monkeypatch):
    conn = _conn(tmp_path)
    pid = _add(conn)
    _no_network(monkeypatch, "The posting is gone: the site answered HTTP 410.")
    out = api.show_posting(conn, ws_cfg, pid)
    assert out["description"] == ""
    assert "410" in out["description_note"]


def test_a_listing_with_no_link_is_explained(tmp_path, ws_cfg, monkeypatch):
    conn = _conn(tmp_path)
    pid = _add(conn, url="")
    _no_network(monkeypatch, "This listing has no link, so there is nothing to open.")
    out = api.show_posting(conn, ws_cfg, pid)
    assert "no link" in out["description_note"]


def test_a_description_means_no_note(tmp_path, ws_cfg, monkeypatch):
    conn = _conn(tmp_path)
    pid = _add(conn)
    _no_network(monkeypatch, "unused", text="A real description. " * 40)
    out = api.show_posting(conn, ws_cfg, pid)
    assert len(out["description"]) > 400
    assert "description_note" not in out, "there is text to read; say nothing"


def test_an_extractor_that_fails_entirely_still_leaves_a_sentence(tmp_path, ws_cfg,
                                                                 monkeypatch):
    """Whatever goes wrong, the pane must not be a blank box."""
    from jobscout import detail
    conn = _conn(tmp_path)
    pid = _add(conn)

    def boom(*a, **k):
        raise RuntimeError("extractor exploded")

    monkeypatch.setattr(detail, "html_detail", boom)
    monkeypatch.setattr(detail, "html_detail_verbose", boom)
    out = api.show_posting(conn, ws_cfg, pid)
    assert out["description_note"], "a fallback sentence is required"


def test_known_deadness_is_used_as_the_explanation(tmp_path, ws_cfg, monkeypatch):
    """A liveness check already recorded why; do not throw that away."""
    conn = _conn(tmp_path)
    pid = _add(conn)
    conn.execute("UPDATE postings SET verify_status='dead', "
                 "verify_note='HTTP 404: no longer accepting applications' "
                 "WHERE id=?", (pid,))
    conn.commit()
    _no_network(monkeypatch, "")
    out = api.show_posting(conn, ws_cfg, pid)
    assert "no longer accepting" in out["description_note"]
