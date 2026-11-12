"""The shared shortlist: a PDF that opens anywhere and says what matters."""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from jobscout import db as dbm
from jobscout.models import Posting
from jobscout.score import score_posting
from jobscout.shortlist import build_pdf, default_path

PREFS = {"title_terms": ["software"], "min_score": 0}

ROW = {
    "title": "Softwareentwickler PHP (m/w/d)",
    "company": "Müller & Söhne GmbH",
    "location": "München",
    "salary": "€52.000–62.000 / Jahr",
    "salary_raw": "",
    "contract": "permanent",
    "deadline_at": "2026-12-23",
    "closes_in_days": 11,
    "url": "https://example.test/jobs/1",
    "note": "Nähe zu Hause, Stack passt.",
}


def test_the_pdf_carries_the_facts_and_the_note(tmp_path):
    out = build_pdf([ROW], "Default", tmp_path / "s.pdf")
    assert out.exists() and out.stat().st_size > 1000
    assert out.read_bytes()[:5] == b"%PDF-"
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(out)).pages)
    assert "Softwareentwickler PHP" in text
    assert "Müller & Söhne" in text
    assert "München" in text
    assert "permanent" in text
    assert "Deadline 2026-12-23" in text
    assert "Nähe zu Hause" in text
    assert "example.test" in text


def test_an_empty_shortlist_is_still_a_document(tmp_path):
    out = build_pdf([], "Default", tmp_path / "empty.pdf")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(out)).pages)
    assert "0 adverts" in text


def test_the_shortlist_keeps_the_order_you_picked(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    run = dbm.start_run(conn)
    ids = []
    for i, title in enumerate(("Erster Job", "Zweiter Job", "Dritter Job")):
        p = Posting(source="xing", title=title, company="ACME GmbH",
                    location="Berlin", url=f"https://x/{i}", external_id=str(i))
        pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
        ids.append(pid)
    picked = [ids[2], ids[0]]
    from jobscout import api
    rows = api.shortlist_rows(conn, picked)
    assert [r["title"] for r in rows] == ["Dritter Job", "Erster Job"]


def test_your_note_travels_with_the_advert(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    run = dbm.start_run(conn)
    p = Posting(source="xing", title="Softwareentwickler", company="ACME GmbH",
                location="Berlin", url="https://x/1", external_id="1")
    pid, _, _ = dbm.upsert(conn, p, score_posting(PREFS, p), run, "t")
    from jobscout import api
    api.save_application(conn, pid, {"stage": "applied"})
    dbm.add_application_note(conn, pid, "Recruiterin heißt Sara.")
    rows = api.shortlist_rows(conn, [pid])
    assert rows[0]["note"] == "Recruiterin heißt Sara."


def test_the_file_lands_somewhere_a_person_looks():
    path = default_path(Path("/tmp"))
    assert path.name.startswith("jobscout-shortlist-")
    assert path.suffix == ".pdf"
    assert path.parent in (Path.home() / "Downloads", Path.home())


def test_the_link_is_a_short_label_and_the_url_is_an_annotation(tmp_path):
    """A 130-character board URL used to print and wrap, and only its first line
    came out clickable because reportlab anchors each fragment separately. The
    label is short now; the full URL lives in the link annotation."""
    row = dict(ROW)
    row["url"] = "https://www.example.test/jobs/" + "x" * 90
    out = build_pdf([row], "Default", tmp_path / "s.pdf")
    page = PdfReader(str(out)).pages[0]
    text = page.extract_text() or ""
    assert "example.test · open advert" in text
    assert "https://www.example.test" not in text
    uris = []
    for ref in page.get("/Annots") or []:
        action = ref.get_object().get("/A")
        if action and action.get("/URI"):
            uris.append(str(action["/URI"]))
    assert "https://www.example.test/jobs/" + "x" * 90 in uris
