"""Dice: dice.com, the US tech board.

robots.txt disallows the /job prefix. This project treats a robots file as a
wish rather than a wall; what matters is whether a plain client can read the
pages, and Dice answers 200 from nginx with no Cloudflare challenge.

Fixtures are trimmed from the live site on 2026-10-21: a standard card, a
sponsored card (different layout, employer in a title attribute, plus a paragraph
of the advert), and one real job page with its JSON-LD.
"""
import re
from datetime import date, timedelta
from pathlib import Path

from jobscout.sources import dice

FIX = Path(__file__).parent / "fixtures"


def _cards():
    return dice.parse_cards((FIX / "dice_cards.html").read_text(encoding="utf-8"))


def test_a_standard_card_becomes_a_posting():
    card = _cards()[0]
    assert card.source == "dice"
    assert card.title == "Software Engineer / Developer"
    assert card.company == "Tandym Tech"
    assert card.location == "Queens, New York"
    assert card.compensation == "$70 - $70 per hour"
    assert card.posted_at == date.today().isoformat(), "the card says Today"
    assert card.url.startswith("https://www.dice.com/job-detail/")
    assert re.fullmatch(r"[0-9a-f-]{36}", card.external_id), "the board's own guid"


def test_a_sponsored_card_is_read_too():
    """The second layout has no test id on the employer, and carries a snippet."""
    sponsored = [c for c in _cards() if c.company == "Top Prospect Group"]
    assert sponsored, "the sponsored card must still become a posting"
    card = sponsored[0]
    assert card.location == "New York, New York", "the employer is not the place"
    assert len(card.description) > 120, "its paragraph is worth keeping for scoring"
    assert card.compensation == "", "a rate inside prose is not a published band"


def test_the_flight_payload_is_not_mistaken_for_cards():
    """Dice embeds escaped copies of its cards in <script>; only rendered ones count."""
    rendered = (FIX / "dice_cards.html").read_text(encoding="utf-8")
    payload = ('<script>self.__next_f.push([1,"<div data-testid=\'job-card\'>'
               '<a href=\'/job-detail/00000000-0000-0000-0000-000000000000\'>'
               'Payload Ghost</a></div>"])</script>')
    html = payload + rendered
    titles = [c.title for c in dice.parse_cards(html)]
    assert "Payload Ghost" not in titles
    assert len(titles) == 2


def test_the_relative_ages_become_dates():
    today = date.today()
    assert dice._posted_at("Today") == today.isoformat()
    assert dice._posted_at("Yesterday") == (today - timedelta(days=1)).isoformat()
    assert dice._posted_at("15d ago") == (today - timedelta(days=15)).isoformat()
    assert dice._posted_at("2w ago") == (today - timedelta(days=14)).isoformat()
    assert dice._posted_at("5 days ago") == (today - timedelta(days=5)).isoformat()
    assert dice._posted_at("3h ago") == today.isoformat()
    assert dice._posted_at("Depends on Experience") == ""


def test_the_job_page_yields_the_advert():
    p = dice.parse_job((FIX / "dice_job.html").read_text(encoding="utf-8"),
                       "https://www.dice.com/job-detail/x")
    assert p is not None
    assert p.title == "Software Engineer (Data), AI Platform"
    assert p.company == "The Atlantic Group"
    assert p.location == "New York, NY, US"
    assert p.compensation == "USD 180000 - 250000"
    assert p.posted_at == "2026-09-01"
    assert len(p.description) > 2000
    assert p.extra["employment"] == "FULL_TIME"


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.errors = []
        self.seen = []

    def get(self, url):
        self.seen.append(url)
        return self.pages.get(url, (200, ""))


def test_fetch_walks_the_query_pages():
    html = (FIX / "dice_cards.html").read_text(encoding="utf-8")
    base = ("https://www.dice.com/jobs?q=software+engineer"
            "&location=New+York%2C+NY&page=")
    f = FakeFetcher({base + "1": (200, html), base + "2": (200, "")})
    out = dice.fetch({"queries": [{"keywords": "software engineer",
                                   "location": "New York, NY", "pages": 3}]}, f)
    assert len(out) == 2
    assert f.seen == [base + "1", base + "2"], "an empty page ends that query"


def test_a_dead_query_is_reported_not_fatal():
    html = (FIX / "dice_cards.html").read_text(encoding="utf-8")
    f = FakeFetcher({
        "https://www.dice.com/jobs?q=software+engineer&location=&page=1": (503, ""),
        "https://www.dice.com/jobs?q=react+developer&location=&page=1": (200, html),
    })
    out = dice.fetch({"queries": [
        {"keywords": "software engineer", "pages": 1},
        {"keywords": "react developer", "pages": 1},
    ]}, f)
    assert len(out) == 2
    assert any("503" in e for e in f.errors)


def test_duplicates_across_queries_are_dropped():
    html = (FIX / "dice_cards.html").read_text(encoding="utf-8")
    f = FakeFetcher({})
    f.pages = {u: (200, html) for u in (
        "https://www.dice.com/jobs?q=software+engineer&location=&page=1",
        "https://www.dice.com/jobs?q=react+developer&location=&page=1",
    )}
    out = dice.fetch({"queries": [
        {"keywords": "software engineer", "pages": 1},
        {"keywords": "react developer", "pages": 1},
    ]}, f)
    assert len(out) == 2, "the same two roles on both queries are two postings"


def test_the_detail_probe_gets_the_full_advert():
    html = (FIX / "dice_job.html").read_text(encoding="utf-8")

    class F:
        errors = []

        def get(self, url):
            return 200, html

    assert len(dice.fetch_detail(_cards()[0], F()) or "") > 2000
