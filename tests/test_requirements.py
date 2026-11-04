"""What an advert says in a sentence, and what it only mentions.

The AI signal is the sharp case: "Erfahrung mit KI von Vorteil" and "Sie muessen
Erfahrung mit KI mitbringen" contain the same words and mean opposite things.
"""
from __future__ import annotations

from jobscout.requirements import ai_signal, extract
from jobscout.score import score_posting


def test_a_bonus_mention_is_not_a_requirement():
    text = ("Wir arbeiten modern. Erfahrung mit KI von Vorteil. "
            "Sie entwickeln Backends in PHP.")
    assert ai_signal(text) == "bonus"
    assert not [r for r in extract(text) if r["kind"] == "ai"]


def test_a_requirement_sentence_is_one():
    text = ("Sie muessen Erfahrung mit KI mitbringen und LLMs im Alltag "
            "einsetzen. Wir bieten einen schoenen Arbeitsplatz.")
    assert ai_signal(text) == "required"
    assert [r["label"] for r in extract(text) if r["kind"] == "ai"] == ["AI required"]


def test_english_requirement():
    assert ai_signal("You must have hands-on experience with LLMs.") == "required"
    assert ai_signal("Exposure to machine learning is a nice to have.") == "bonus"


def test_silence_is_not_a_no():
    assert ai_signal("Wir suchen einen PHP-Entwickler fuer unser Team.") == ""
    assert ai_signal("") == ""


def _posting(description: str):
    from jobscout.models import Posting
    return Posting(source="x", title="Backend Developer", company="A",
                   location="Berlin", url="https://x/1",
                   description=description, external_id="1")


def test_the_filter_is_strict_about_silence():
    prefs = {"require_ai": True, "title_terms": ["developer"],
             "title_deny": [], "country_allow": [], "language_allow": []}
    long_no_ai = "Wir bauen Software. " * 20
    s = score_posting(prefs, _posting(long_no_ai))
    assert s.filtered and s.reason == "ai:not_stated"

    # No text at all: we cannot claim it states the requirement, so it is out.
    s = score_posting(prefs, _posting(""))
    assert s.filtered and s.reason == "ai:not_stated"

    kept = score_posting(prefs, _posting(
        "Du baust Services. Du musst Erfahrung mit KI mitbringen. " * 3))
    assert not kept.filtered
