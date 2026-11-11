"""The Max Planck Society's own jobboard: cards, places and the advert body."""
from __future__ import annotations

from pathlib import Path

from jobscout import sources
from jobscout.models import Posting
from jobscout.sources import maxplanck

FIX = Path(__file__).resolve().parent / "fixtures"


class _Fetcher:
    """Just enough of the real Fetcher for a parser test."""

    def __init__(self, body: str, status: int = 200):
        self.body, self.status, self.errors = body, status, []

    def get(self, url, headers=None):
        return self.status, self.body


def test_the_card_gives_title_institute_city_and_date():
    rows = maxplanck.parse((FIX / "maxplanck_list_2026.html").read_text(encoding="utf-8"))
    assert len(rows) == 3
    first = rows[0]
    assert first.source == "maxplanck"
    assert first.title == "Scientific software engineer (f/m/d) in SWI Science Operations"
    assert first.company == "Max Planck Institute for Solar System Research"
    assert first.location == "Göttingen"
    assert first.url.startswith("https://www.mpg.de/27081711/")
    assert first.posted_at == "2026-11-10"


def test_an_institute_name_with_a_comma_keeps_its_city():
    rows = maxplanck.parse((FIX / "maxplanck_list_2026.html").read_text(encoding="utf-8"))
    # "Max Planck Institute of Animal Behavior, Radolfzell / Constance"
    assert rows[2].company == "Max Planck Institute of Animal Behavior"
    assert rows[2].location == "Radolfzell / Constance"


def test_the_detail_page_gives_the_advert_not_the_header():
    body = (FIX / "maxplanck_detail_2026.html").read_text(encoding="utf-8")
    p = Posting(source="maxplanck", title="x", company="y", location="z",
                url="https://www.mpg.de/27081711/x", external_id="27081711")
    text = maxplanck.fetch_detail(p, _Fetcher(body))
    assert text and len(text) > 2000
    assert "Max Planck Institute for Solar System Research" in text
    # the header block is not repeated as prose
    assert text.count("Scientific software engineer (f/m/d) in SWI Science Operations") <= 2
    assert "Job Offer from November 10, 2026" not in text


def test_a_missing_page_is_a_teaser_not_an_exception():
    p = Posting(source="maxplanck", title="x", company="y", location="z",
                url="https://www.mpg.de/1/x", external_id="1")
    assert maxplanck.fetch_detail(p, _Fetcher("", status=404)) is None


def test_the_board_is_the_employers_own():
    assert "maxplanck" in sources.REGISTRY
    assert sources.group_of("maxplanck") == sources.GROUP_ACADEMIC
    assert sources.is_direct("maxplanck")


def test_a_city_the_board_prints_twice_is_one_city():
    p = maxplanck.parse(
        '<li class="teaser teaser-horizontal">'
        '<h3><a href="/27070000/x">Head of IT (m/f/d)</a></h3>'
        '<div class="data"><span class="date">November 9, 2026</span></div>'
        '<div>Max Planck Institute for Intelligent Systems, Stuttgart, Stuttgart</div>'
        '</li>')
    assert p[0].company == "Max Planck Institute for Intelligent Systems"
    assert p[0].location == "Stuttgart"
