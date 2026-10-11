import json
from datetime import date

from jobscout.textutil import (balanced_json, clean_html, clean_html_lines,
                               date_part, de_rel_date, iso_from_epoch, norm,
                               slug, snippet)


def test_clean_html_strips_style_and_script_bodies():
    assert clean_html("<style>.x{color:red}</style><p>Hello <b>World</b></p>") == "Hello World"
    assert clean_html("<script>alert(1)</script>Hi") == "Hi"


def test_clean_html_lines_keeps_breaks():
    out = clean_html_lines("<p>Line one</p><br/>Line two")
    assert out.splitlines() == ["Line one", "Line two"]
    assert json.dumps(out)  # serialisable


def test_clean_html_lines_handles_bare_paragraph_tags():
    # HN separates paragraphs with <p> opening tags only (no closing tag);
    # text from two paragraphs must never glue together.
    out = clean_html_lines("One | Remote<p>Join us today<p>More text")
    assert out.splitlines() == ["One | Remote", "Join us today", "More text"]


def test_norm_and_slug():
    assert norm("  Senior   Developer ") == "senior developer"
    assert slug("Zürich / Software-Entwickler (m/w/d)") == "zuerich-software-entwickler-m-w-d"
    assert slug("München") == "muenchen"


def test_date_part():
    assert date_part("2026-09-15T10:00:00Z") == "2026-09-15"
    assert date_part("vor 3 Tagen") == "vor 3 Tagen"
    assert date_part("") == ""


def test_iso_from_epoch_seconds_and_ms():
    assert iso_from_epoch(0) == "1970-01-01"
    assert iso_from_epoch(1565990241800).startswith("2019-")
    assert iso_from_epoch("1789673422").startswith("2026-")
    assert iso_from_epoch("junk") == ""


def test_de_rel_date():
    t = date(2026, 9, 17)
    assert de_rel_date("Gestern", t) == "2026-09-16"
    assert de_rel_date("Heute veröffentlicht", t) == "2026-09-17"
    assert de_rel_date("vor 3 Tagen veröffentlicht", t) == "2026-09-14"
    assert de_rel_date("vor 2 Wochen", t) == "2026-09-03"
    assert de_rel_date("vor einem Monat", t) == "2026-08-18"
    assert de_rel_date("2 days ago", t) == "2026-09-15"
    assert de_rel_date("irgendwann", t) == ""


def test_snippet_truncates():
    assert snippet("a" * 300, 20).endswith("\u2026")
    assert snippet("short", 20) == "short"


def test_balanced_json():
    obj, end = balanced_json('var x = {"a": {"b": "}"}}; more', 0)
    assert json.loads(obj) == {"a": {"b": "}"}}
    assert "more" in 'var x = {"a": {"b": "}"}}; more'[end:]
