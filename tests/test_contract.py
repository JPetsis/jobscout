"""One canonical word per contract fact, in every language we cover.

A DACH search is not one language: the Austrian and Swiss words for a permanent
post are not the German ones, and a list that only knows "unbefristet" quietly
misses half of Vienna. These tests pin the table, the way the config ships it,
and the fact that the chip shows the canonical word while the breakdown keeps
the evidence.
"""
import yaml

from jobscout import contract
from jobscout.config import DEFAULT_CONFIG
from jobscout.score import score_posting
from jobscout.models import Posting


def _prefs():
    return {"title_terms": [], "require_title_match": False,
            "permanent_terms": list(contract.PERMANENT_TERMS),
            "permanent_boost": 4, "permanent_cap": 4,
            "temporary_terms": list(contract.TEMPORARY_TERMS)}


def _score(description):
    p = Posting(source="x", title="Mitarbeiter (m/w/d)", company="ACME",
                location="Wien", url="https://x/1", external_id="1",
                description=description)
    return score_posting(_prefs(), p)


def test_no_phrase_belongs_to_two_canonical_words():
    seen = {}
    for word, phrases in contract.VARIANTS.items():
        for phrase in phrases:
            assert phrase == phrase.lower().strip(), phrase
            assert phrase not in seen, f"{phrase!r} is in {seen.get(phrase)} and {word}"
            seen[phrase] = word


def test_the_shipped_config_lists_exactly_the_table():
    """A term in one list and not the other is a term that silently stops
    matching, which is exactly how "fixe Anstellung" would go missing."""
    prefs = yaml.safe_load(DEFAULT_CONFIG)["preferences"]
    assert list(prefs["permanent_terms"]) == list(contract.PERMANENT_TERMS)
    assert list(prefs["temporary_terms"]) == list(contract.TEMPORARY_TERMS)


def test_the_regional_words_are_recognised():
    """Germany's word, Austria/Switzerland's ("fix", Planstelle, Dauerstelle),
    and the English ones, all land on the same fact."""
    for phrase in ("unbefristete Stelle", "Festanstellung",
                   "feste Anstellung", "fixe Anstellung",
                   "Dauerstelle", "Planstelle",
                   "permanent contract", "open-ended contract",
                   "contract of indefinite duration"):
        s = _score(phrase)
        assert s.reasons and s.reasons[0][0].startswith("perm:"), phrase
        assert contract.from_reasons(s.reasons) == contract.PERMANENT, phrase


def test_the_fixed_term_words_are_recognised():
    for phrase in ("befristet für 2 Jahre", "Zeitvertrag",
                   "Elternzeitvertretung", "Karenzvertretung",
                   "fixed-term contract", "temporary role",
                   "maternity cover", "Temporärstelle"):
        s = _score(phrase)
        assert s.reasons and s.reasons[0][0].startswith("tmp:"), phrase
        assert contract.from_reasons(s.reasons) == contract.FIXED_TERM, phrase


def test_unbefristet_never_reads_as_fixed_term():
    """"befristet" sits inside "unbefristet"; the scorer's elif is what stops
    the same advert from carrying both chips."""
    s = _score("unbefristete Anstellung in Vollzeit")
    assert [r[0] for r in s.reasons if r[0].startswith(("perm:", "tmp:"))] == [
        "perm:unbefristet"]


def test_nothing_stated_means_no_chip():
    s = _score("Wir suchen Verstärkung für unser Team.")
    assert contract.from_reasons(s.reasons) == ""
    assert contract.label("something else") == ""
