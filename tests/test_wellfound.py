"""Wellfound: wellfound.com role pages.

robots.txt allows the job pages; they are server-rendered. A card is a COMPANY
block that can hold several roles, so the parser must read one employer and
several postings out of it. Fixtures are trimmed from the live site on
2026-10-21: one real company card with three roles, and one job page with its
schema.org JobPosting.
"""
from pathlib import Path

from jobscout.sources import wellfound

FIX = Path(__file__).parent / "fixtures"


def _cards():
    return wellfound.parse_cards((FIX / "wellfound_cards.html").read_text(encoding="utf-8"))


def test_one_company_card_holds_every_role():
    cards = _cards()
    assert len(cards) == 3, "the card lists three openings for the same employer"
    assert {c.company for c in cards} == {"Noldor"}, "the employer is read once per card"
    assert len({c.url for c in cards}) == 3


def test_a_role_carries_its_band_and_place():
    first = _cards()[0]
    assert first.source == "wellfound"
    assert first.title == "Senior Software Engineer - Client Lifecycle"
    assert first.compensation == "$162k \u2013 $233k"
    assert first.location == "Remote only \u2022 United States"
    assert first.url.startswith("https://wellfound.com/jobs/4855345-")
    assert first.external_id == "4855345"
    assert first.posted_at, "cards say '4 days ago', which is a date"
    assert first.description == "", "cards carry no description; the probe fills it"


def test_equity_after_the_band_does_not_become_the_band():
    for card in _cards():
        assert card.compensation.startswith("$"), card.compensation
        assert "%" not in card.compensation, "equity is not salary"


def test_the_job_page_yields_the_advert():
    p = wellfound.parse_job((FIX / "wellfound_job.html").read_text(encoding="utf-8"),
                            "https://wellfound.com/jobs/4855345")
    assert p is not None
    assert p.title == "Senior Software Engineer - Client Lifecycle"
    assert p.company == "Virta Health"
    assert p.location == "Seattle, Washington, United States"
    assert p.compensation == "USD 162000 - 233000 yearly"
    assert p.posted_at == "2026-10-16"
    assert len(p.description) > 3000


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.errors = []
        self.seen = []

    def get(self, url):
        self.seen.append(url)
        return self.pages.get(url, (200, ""))


def test_fetch_walks_pages_until_one_is_empty():
    html = (FIX / "wellfound_cards.html").read_text(encoding="utf-8")
    f = FakeFetcher({
        "https://wellfound.com/role/r/software-engineer?page=1": (200, html),
        "https://wellfound.com/role/r/software-engineer?page=2": (200, ""),
    })
    out = wellfound.fetch({"roles": ["software-engineer"], "max_pages": 5}, f)
    assert len(out) == 3
    assert len(f.seen) == 2, "an empty page ends the walk, the rest is not requested"


def test_fetch_reports_a_dead_page_and_keeps_going():
    html = (FIX / "wellfound_cards.html").read_text(encoding="utf-8")
    f = FakeFetcher({
        "https://wellfound.com/role/r/software-engineer?page=1": (503, ""),
        "https://wellfound.com/role/r/data-engineer?page=1": (200, html),
    })
    out = wellfound.fetch({"roles": ["software-engineer", "data-engineer"], "max_pages": 1}, f)
    assert len(out) == 3, "one dead role does not sink the scan"
    assert any("503" in e for e in f.errors)


def test_the_detail_probe_gets_the_full_advert():
    html = (FIX / "wellfound_job.html").read_text(encoding="utf-8")

    class F:
        errors = []

        def get(self, url):
            return 200, html

    assert len(wellfound.fetch_detail(_cards()[0], F()) or "") > 3000
