"""Y Combinator: the public job board on ycombinator.com/jobs.

robots.txt allows the board, the role pages are server-rendered, and each job
page carries a schema.org JobPosting for the detail probe. Fixtures are trimmed
from the live site on 2026-10-21: two adjacent cards from
/jobs/role/software-engineer and one real job page with its JSON-LD.
"""
from pathlib import Path

from jobscout.sources import ycombinator

FIX = Path(__file__).parent / "fixtures"


def _cards():
    return ycombinator.parse_cards((FIX / "ycombinator_cards.html").read_text(encoding="utf-8"))


def test_a_card_becomes_a_posting():
    cards = _cards()
    assert len(cards) == 2
    first = cards[0]
    assert first.source == "ycombinator"
    assert first.title == "Senior Data Engineer"
    assert first.company == "Vector (W23)", "the batch tag is part of the name on the board"
    assert first.location == "Boston, MA"
    assert first.compensation == "$180K - $230K"
    assert first.url == ("https://www.ycombinator.com/companies/vector/jobs/"
                         "MZ9Oh9B-senior-data-engineer")
    assert first.external_id == "MZ9Oh9B"
    assert first.description == "", "cards carry no description; the probe fills it"


def test_the_relative_age_becomes_a_date():
    from datetime import date, timedelta

    assert ycombinator._posted_at("(5 days ago)") == (date.today() - timedelta(days=5)).isoformat()
    assert ycombinator._posted_at("( about 10 hours ago)") == date.today().isoformat()
    assert ycombinator._posted_at("(a day ago)") == (date.today() - timedelta(days=1)).isoformat()
    assert ycombinator._posted_at("no age here") == ""


def test_the_apply_button_is_not_part_of_the_place():
    for card in _cards():
        assert not card.location.endswith(("Apply", "Save"))
    assert all(card.location for card in _cards())


def test_the_job_page_yields_the_advert():
    p = ycombinator.parse_job((FIX / "ycombinator_job.html").read_text(encoding="utf-8"),
                              "https://www.ycombinator.com/companies/type/jobs/x")
    assert p is not None
    assert p.title == "Founding Product Engineer"
    assert p.company == "Type"
    assert p.location == "New York, New York, US"
    assert p.posted_at == "2025-06-04"
    assert p.compensation == "USD 140000 - 230000 yearly"
    assert len(p.description) > 2000 and "product" in p.description.lower()
    assert p.extra["employment"] == "FULL_TIME"


class FakeFetcher:
    """The scan's fetcher, minus the network."""

    def __init__(self, pages, status=200):
        self.pages = pages
        self.status = status
        self.errors = []
        self.seen = []

    def get(self, url):
        self.seen.append(url)
        return self.status, self.pages.get(url, self.pages.get("*", ""))


def test_fetch_dedupes_and_respects_max_jobs():
    html = (FIX / "ycombinator_cards.html").read_text(encoding="utf-8")
    f = FakeFetcher({"*": html})
    out = ycombinator.fetch({"roles": ["software-engineer"], "locations": [], "max_jobs": 1}, f)
    assert len(out) == 1, "max_jobs caps the scan's share"
    assert f.seen == ["https://www.ycombinator.com/jobs/role/software-engineer"]


def test_fetch_walks_role_and_city_paths_without_duplicates():
    html = (FIX / "ycombinator_cards.html").read_text(encoding="utf-8")
    f = FakeFetcher({"*": html})
    out = ycombinator.fetch({"roles": ["software-engineer"], "locations": ["new-york"]}, f)
    assert len(out) == 2, "the same two roles on both paths are one posting each"
    assert f.seen == [
        "https://www.ycombinator.com/jobs/role/software-engineer",
        "https://www.ycombinator.com/jobs/role/software-engineer/new-york",
    ]


def test_a_dead_page_is_reported_not_fatal():
    html = (FIX / "ycombinator_cards.html").read_text(encoding="utf-8")
    f = FakeFetcher({"*": html}, status=503)
    assert ycombinator.fetch({"roles": ["software-engineer"]}, f) == []
    assert any("503" in e for e in f.errors)


def test_the_detail_probe_gets_the_full_advert():
    html = (FIX / "ycombinator_job.html").read_text(encoding="utf-8")
    card = _cards()[0]

    class F:
        errors = []

        def get(self, url):
            return 200, html

    text = ycombinator.fetch_detail(card, F())
    assert text and len(text) > 2000


# --- the payload, which is where the posting's own date lives ----------------

def _payload_cards():
    return ycombinator.parse_cards(
        (FIX / "ycombinator_payload.html").read_text(encoding="utf-8"))


def test_the_payload_dates_the_posting_not_the_listing():
    """The bug this guards: the markup's "(about 2 hours ago)" is the feed's
    activity, so a role created in 2020 looked brand new and sailed past
    max_age_days."""
    from datetime import date, timedelta

    old, new = _payload_cards()
    assert old.title == "Staff Full Stack Engineer"
    assert old.company == "Sully (S21)"
    assert old.location == "Mountain View, CA, US"
    assert old.compensation == "$180K - $240K"
    assert old.url == ("https://www.ycombinator.com/companies/sully/jobs/"
                       "abc123-staff-full-stack-engineer")
    assert old.external_id == "abc123"
    assert old.posted_at == (date.today() - timedelta(days=90)).isoformat(), \
        "3 months old, even though the listing was touched two hours ago"
    assert new.posted_at == date.today().isoformat()


def test_the_markup_alone_claims_no_date():
    """Without the payload the only date in the page is the feed's activity, so
    the fallback stores none rather than passing it off as the posting date."""
    assert all(card.posted_at == "" for card in _cards())
