from jobscout import db as dbm
from jobscout import triage
from jobscout.models import Posting
from jobscout.score import score_posting

PREFS = {"title_terms": ["entwickler"], "title_boost": 3, "keywords": [], "exclude_terms": []}


def _rows(tmp_path, n=3):
    conn = dbm.connect(tmp_path / "t.db")
    run = dbm.start_run(conn)
    ids = []
    for i in range(n):
        p = Posting(source="t", title=f"Softwareentwickler {i}", company=f"C{i}",
                    external_id=str(i), url=f"https://x/{i}")
        pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
        ids.append(pid)
    return conn, ids


def test_triage_marks_opens_and_skips(tmp_path):
    conn, ids = _rows(tmp_path)
    rows = dbm.list_postings(conn, status="new")
    first, second, third = rows[0], rows[1], rows[2]
    script = iter(["o", "s", "n", "a"])
    opened, out = [], []
    counts = triage.run(rows, decide=lambda pid, st: dbm.set_status(conn, pid, st),
                        opener=opened.append, reader=lambda: next(script), write=out.append)
    assert counts == {"saved": 1, "applied": 1, "dismissed": 0, "skipped": 1,
                      "opened": 1, "quit": False, "total": 3}
    assert opened == [first["url"]]
    assert dbm.get_posting(conn, first["id"])["status"] == "saved"
    assert dbm.get_posting(conn, second["id"])["status"] == "new"      # skipped, untouched
    assert dbm.get_posting(conn, third["id"])["status"] == "applied"
    assert any(first["title"] in line for line in out)


def test_triage_unknown_key_reprompts_then_quit(tmp_path):
    conn, ids = _rows(tmp_path)
    rows = dbm.list_postings(conn, status="new")
    script = iter(["z", "x", "q"])
    out = []
    counts = triage.run(rows, decide=lambda pid, st: dbm.set_status(conn, pid, st),
                        opener=lambda u: None, reader=lambda: next(script), write=out.append)
    assert counts["dismissed"] == 1 and counts["quit"] is True
    assert dbm.get_posting(conn, rows[0]["id"])["status"] == "dismissed"
    assert dbm.get_posting(conn, rows[1]["id"])["status"] == "new"


def test_triage_eof_stops_cleanly(tmp_path):
    conn, ids = _rows(tmp_path)
    rows = dbm.list_postings(conn, status="new")

    def reader():
        raise EOFError

    counts = triage.run(rows, decide=lambda pid, st: None, opener=lambda u: None,
                        reader=reader, write=lambda *a, **k: None)
    assert counts["quit"] is True and counts["total"] == 3


def test_triage_with_nothing_to_do():
    out = []
    counts = triage.run([], decide=lambda pid, st: None, opener=lambda u: None,
                        reader=lambda: "n", write=out.append)
    assert counts["total"] == 0
    assert any("nothing to triage" in line for line in out)


def test_summary_text():
    counts = {"saved": 2, "applied": 1, "dismissed": 3, "skipped": 0, "opened": 4,
              "quit": False, "total": 10}
    text = triage.summary_text(counts)
    assert "2 saved" in text and "1 applied" in text and "4 opened" in text
    assert "stopped early" not in text
    counts["quit"] = True
    assert "stopped early" in triage.summary_text(counts)
