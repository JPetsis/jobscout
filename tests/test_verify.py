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
           status="new", filtered=0, company="ACME", location=""):
    # The fingerprint is source+title+company+location, so two rows with the
    # same title would collapse into one -- the title carries the URL instead.
    run = dbm.start_run(conn)
    p = Posting(source=source, title=title or f"Engineer {url}",
                company=company, location=location, url=url, external_id=ext)
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
    assert dbm.get_posting(conn, gone) is None, \
        "absence from a complete board list is evidence, and dead rows are removed"
    assert out["purged"] == 1


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


def test_one_post_on_two_boards_keeps_one_row(tmp_path):
    """jobs.ac.uk and THE both carried the Cambridge museum directorship, word
    for word. Merging those is right; merging two different employers is not."""
    conn = dbm.connect(tmp_path / "t.db")
    title = "Directorship of the Museum of Archaeology and Anthropology"
    a = _store(conn, url="https://a/1", source="jobsacuk", title=title,
               company="University of Cambridge - Department of Archaeology",
               location="Cambridge")
    b = _store(conn, url="https://b/1", source="theunijobs", title=title,
               company="UNIVERSITY OF CAMBRIDGE", location="Cambridge")
    assert dbm.dedupe_cross_source(conn, {"greenhouse"}) == 1
    assert conn.execute("SELECT dup_of FROM postings WHERE id=?", (b,)).fetchone()["dup_of"] == a
    # the same title at another university in the same city is another job
    c = _store(conn, url="https://c/1", source="theunijobs", title=title,
               company="University of Oxford", location="Cambridge")
    assert dbm.dedupe_cross_source(conn, {"greenhouse"}) == 0
    assert conn.execute("SELECT dup_of FROM postings WHERE id=?", (c,)).fetchone()["dup_of"] is None


def test_dead_rows_are_removed_but_what_you_acted_on_stays(tmp_path):
    """A dead advert has no use on a board. An application is a record of
    something the user did, and the Applications pane joins on posting_id, so
    deleting that row would erase it."""
    conn = dbm.connect(tmp_path / "t.db")
    gone_new = _store(conn, url="https://x/gone1")
    gone_saved = _store(conn, url="https://x/gone2", status="saved")
    gone_applied = _store(conn, url="https://x/gone3")
    dbm.set_status(conn, gone_applied, "applied")
    from jobscout import api as apimod
    apimod.save_application(conn, gone_applied, {})

    for pid in (gone_new, gone_saved, gone_applied):
        dbm.update_verify(conn, pid, "dead", "HTTP 410")

    purged = dbm.purge_dead(conn)
    left = {r["id"] for r in conn.execute("SELECT id FROM postings")}
    assert purged == 1
    assert gone_new not in left
    assert gone_saved in left, "you saved it"
    assert gone_applied in left, "an application points at it"
    assert apimod.list_applications(conn), "the logger still has its record"


def test_a_republished_role_from_one_board_is_marked_duplicate(tmp_path):
    """XING carried the Landesverwaltungsamt role twice: same place, company
    "…amt Berlin" against "…amt Berlin Personalservice", title cut at 100
    characters, new posting id. Neither fingerprint nor alt_key could see it."""
    conn = dbm.connect(tmp_path / "t.db")
    old = _store(conn, source="xing", url="https://www.xing.com/jobs/a-158857672",
                 title="Anwendungsentwickler/in für Programmierung, Betreuung und "
                       "Weiterentwicklung des Beihilfefachverfahrens",
                 ext="158857672")
    conn.execute("UPDATE postings SET company=? WHERE id=?",
                 ("Landesverwaltungsamt Berlin", old))
    conn.commit()
    new = _store(conn, source="xing", url="https://www.xing.com/jobs/b-158906548",
                 title="Anwendungsentwickler/in für Programmierung, Betreuung und "
                       "Weiterentwicklung des Beihilfefachverfahre",
                 ext="158906548")
    conn.execute("UPDATE postings SET company=? WHERE id=?",
                 ("Landesverwaltungsamt Berlin Personalservice", new))
    conn.commit()

    assert dbm.dedupe_same_source(conn) == 1
    row = conn.execute("SELECT dup_of FROM postings WHERE id=?", (new,)).fetchone()
    assert row["dup_of"] == old, "the older advert stays the visible one"


def test_two_different_roles_sharing_a_prefix_are_not_merged(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    _store(conn, source="xing", url="https://x/1", title="Senior Backend Entwickler PHP",
           ext="1")
    _store(conn, source="xing", url="https://x/2", title="Senior Backend Entwickler PHP "
           "und DevOps", ext="2")
    conn.execute("UPDATE postings SET company='Foo GmbH'")
    conn.commit()
    conn.execute("UPDATE postings SET company='Bar AG' WHERE id=2")
    conn.commit()
    assert dbm.dedupe_same_source(conn) == 0


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
                    progress=lambda done, total, counts, host="":
                    seen.append((done, total, counts, host)))
    assert seen[-1][:3] == (3, 3, {"ok": 3, "dead": 0, "unknown": 0, "batched": 0})
    assert seen[-1][3], "the host travels with the progress"
