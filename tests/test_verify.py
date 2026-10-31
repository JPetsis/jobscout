"""The liveness pass: who gets checked, how, and what absence proves.

The rules under test are the ones that decide whether a row is called dead:

* a full board list settles every row from that board in one request, in both
  directions;
* an incomplete list may CONFIRM a row but never kill one;
* rows already checked recently are not checked again;
* a row a scan just returned is alive, including one that had been dead and was
  re-advertised.
"""
import json
import types

from jobscout import db as dbm
from jobscout import verify
from jobscout.models import Posting
from jobscout.score import score_posting

PREFS = {"title_terms": ["engineer"], "title_boost": 3, "keywords": [],
         "exclude_terms": [], "require_title_match": False}


def _cfg(tmp_path, **prefs):
    base = {"min_score": 6, "title_terms": ["engineer"], "host_delay": 0}
    base.update(prefs)
    return types.SimpleNamespace(prefs=base, root=tmp_path)


def _store(conn, *, url, title=None, source="greenhouse", ext="", score=10,
           status="new", filtered=0):
    # The fingerprint is source+title+company+location, so two rows with the
    # same title would collapse into one -- the title carries the URL instead.
    run = dbm.start_run(conn)
    p = Posting(source=source, title=title or f"Engineer {url}",
                company="ACME", url=url, external_id=ext)
    pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    conn.execute("UPDATE postings SET score=?, filtered=?, status=? WHERE id=?",
                 (score, filtered, status, pid))
    conn.commit()
    return pid


class Board:
    """A fake fetcher: one canned body per URL fragment, and a call log."""

    def __init__(self):
        self.calls = []
        self.bodies = {}

    def get(self, url, headers=None, **kw):
        self.calls.append(url)
        for needle, (status, body) in self.bodies.items():
            if needle in url:
                return status, body
        return 404, ""


def _use_board(monkeypatch, board):
    monkeypatch.setattr(verify, "Fetcher", lambda *a, **kw: board)


def test_a_full_board_list_settles_every_row_in_one_request(tmp_path, monkeypatch):
    conn = dbm.connect(tmp_path / "t.db")
    keep1 = _store(conn, url="https://boards.greenhouse.io/acme/jobs/101", ext="101")
    keep2 = _store(conn, url="https://boards.greenhouse.io/acme/jobs/102", ext="102")
    gone = _store(conn, url="https://boards.greenhouse.io/acme/jobs/103", ext="103")

    board = Board()
    board.bodies["boards-api.greenhouse.io/v1/boards/acme/jobs"] = (
        200, '{"jobs":[{"id":101},{"id":102}]}')
    _use_board(monkeypatch, board)

    out = verify.run_pass(_cfg(tmp_path), conn, max_age_days=0)
    assert len(board.calls) == 1, "one request for the whole board"
    assert out["batched"] == 3
    assert dbm.get_posting(conn, keep1)["verify_status"] == "ok"
    assert dbm.get_posting(conn, keep2)["verify_status"] == "ok"
    assert dbm.get_posting(conn, gone)["verify_status"] == "dead", \
        "the board lists every live job, so absence is evidence"


def test_an_incomplete_board_list_never_kills_rows(tmp_path, monkeypatch):
    """A board bigger than the ten-page cap cannot prove absence, so a missing
    id must leave the row unknown instead of killing it."""
    conn = dbm.connect(tmp_path / "t.db")
    pid = _store(conn, url="https://jobs.smartrecruiters.com/BayWaAG/744000158725628",
                 source="smartrecruiters", ext="744000158725628")
    board = Board()
    for offset in range(0, 1000, 100):
        # a real page carries `limit` entries, so offsets move 0/100/.../900
        page = [{"id": "nope-%d-%d" % (offset, i)} for i in range(100)]
        board.bodies[f"offset={offset}"] = (
            200, json.dumps({"totalFound": 5000, "content": page}))
    _use_board(monkeypatch, board)

    out = verify.run_pass(_cfg(tmp_path), conn, max_age_days=0)
    assert dbm.get_posting(conn, pid)["verify_status"] == "unknown"
    assert out["dead"] == 0


def test_a_board_list_that_cannot_be_read_falls_back_to_the_row(tmp_path, monkeypatch):
    conn = dbm.connect(tmp_path / "t.db")
    pid = _store(conn, url="https://boards.greenhouse.io/acme/jobs/101", ext="101")
    board = Board()          # the board list fails; the posting itself answers
    board.bodies["content=false"] = (500, "")
    board.bodies["boards-api.greenhouse.io/v1/boards/acme/jobs/101"] = (200, "{}")
    _use_board(monkeypatch, board)

    verify.run_pass(_cfg(tmp_path), conn, max_age_days=0)
    row = dbm.get_posting(conn, pid)
    assert row["verify_status"] == "ok"
    assert any(c.endswith("/jobs/101") for c in board.calls), \
        "a broken board list must not be taken as absence: the row itself is asked"


def test_only_visible_rows_are_checked(tmp_path, monkeypatch):
    conn = dbm.connect(tmp_path / "t.db")
    visible = _store(conn, url="https://x/1")
    _store(conn, url="https://x/2", score=3)          # under the floor
    _store(conn, url="https://x/3", filtered=1)        # filtered out
    _store(conn, url="https://x/4", status="dismissed")

    board = Board()
    board.bodies["https://x/1"] = (200, "open")
    _use_board(monkeypatch, board)
    out = verify.run_pass(_cfg(tmp_path), conn, max_age_days=0)
    assert out["total"] == 1
    assert board.calls == ["https://x/1"]
    assert dbm.get_posting(conn, visible)["verify_status"] == "ok"


def test_rows_checked_recently_are_skipped(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    pid = _store(conn, url="https://x/1")
    dbm.update_verify(conn, pid, "ok", "")
    assert dbm.postings_to_check(conn, 6, max_age_days=3) == []
    assert [r["id"] for r in dbm.postings_to_check(conn, 6, max_age_days=0)] == [pid]


def test_a_scan_marks_what_it_saw_alive_and_revives_a_dead_row(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    pid = _store(conn, url="https://x/1")
    dbm.update_verify(conn, pid, "dead", "HTTP 410")
    run = dbm.start_run(conn)
    p = Posting(source="greenhouse", title="Engineer", company="ACME",
                url="https://x/1", external_id="")
    dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    row = dbm.get_posting(conn, pid)
    assert row["verify_status"] == "ok", "the board just returned it"
    assert row["verified_at"], "and that counts as a check"


def test_a_rate_limited_host_gets_a_slower_pace_only_for_it():
    """A real pass produced 64 rate-limited rows, all from one host, while the
    other forty were fine. Politeness is per host, not per pass."""
    class F:
        def __init__(self):
            self.calls = []

        def get(self, url, headers=None, **kw):
            self.calls.append((url, kw.get("host_delay")))
            return 200, ""

    f = F()
    paced = verify._Paced(f, {"www.arbeitnow.com": 1.1})
    paced.get("https://www.arbeitnow.com/jobs/x")
    paced.get("https://example.org/jobs/x")
    assert f.calls[0][1] == 1.1
    assert f.calls[1][1] is None, "every other host keeps the shared pace"


def test_progress_is_reported_for_a_bar(tmp_path, monkeypatch):
    conn = dbm.connect(tmp_path / "t.db")
    for i in range(3):
        _store(conn, url=f"https://x/{i}")
    board = Board()
    for i in range(3):
        board.bodies[f"https://x/{i}"] = (200, "open")
    _use_board(monkeypatch, board)
    seen = []
    verify.run_pass(_cfg(tmp_path), conn, max_age_days=0,
                    progress=lambda done, total, counts: seen.append((done, total)))
    assert seen[-1] == (3, 3)
