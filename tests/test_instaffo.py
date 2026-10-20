"""Instaffo: the public job board behind app.instaffo.com.

The candidate API is login-gated (`403 not_allowed`), so the source reads the
public board: ten job cards per list page, newest first, and — only for rows the
scan's detail probe asks about — the server-rendered job page with its schema.org
JobPosting.

Fixtures are trimmed from the live site on 2026-10-20: two cards from
/de/jobs/find (one with a salary badge) and one real job page with its JSON-LD.
"""
from pathlib import Path

from jobscout import salary
from jobscout.sources import instaffo

FIX = Path(__file__).parent / "fixtures"


def _detail() -> str:
    return (FIX / "instaffo_job.html").read_text(encoding="utf-8")


def _cards() -> str:
    return (FIX / "instaffo_cards.html").read_text(encoding="utf-8")


def test_a_card_becomes_a_posting():
    cards = instaffo.parse_cards(_cards())
    assert len(cards) == 2
    first = cards[0]
    assert first.source == "instaffo"
    assert first.title == "Teamleiter Servicetechniker (m/w/d)"
    assert first.company == "ista Express Service GmbH"
    assert first.location == "Leipzig"
    assert first.url == ("https://jobs.instaffo.com/de/job/"
                         "teamleiter-servicetechniker-m-w-d-9e3b3aa8ff13"), "tracking stripped"
    assert first.external_id == "9e3b3aa8ff13"
    assert first.posted_at, "the card says 'vor 16 Stunden', which is a date"
    assert first.description == "", "cards carry no description; the probe fills it"


def test_the_relative_age_becomes_a_date():
    from datetime import date, timedelta

    assert instaffo._posted_at("vor 16 Stunden") == date.today().isoformat()
    assert instaffo._posted_at("vor 3 Tagen") == (date.today() - timedelta(days=3)).isoformat()
    assert instaffo._posted_at("vor einem Tag") == (date.today() - timedelta(days=1)).isoformat()
    assert instaffo._posted_at("gestern") == "", "unknown shapes stay empty"


def test_the_job_page_yields_a_posting():
    p = instaffo.parse_job(_detail(), "https://jobs.instaffo.com/de/job/x")
    assert p is not None
    assert p.title == "Junior IT Consultant (m/f/d)"
    assert p.company == "UCG United Consulting Group GmbH"
    assert p.location == "Frankfurt am Main, DE"
    assert p.posted_at == "2026-08-26"
    assert p.expires_at == "2027-10-20"
    assert "IT-Architekturen" in p.description
    assert p.external_id == "junior-it-consultant-m-f-d-5b3d78bc1bd5"


def test_the_salary_band_is_written_so_the_shared_parser_reads_it():
    """baseSalary is rare on this board, but when it is there it should survive:
    the adapter formats it into `compensation`, which salary.parse already owns."""
    p = instaffo.parse_job(_detail(), "https://jobs.instaffo.com/de/job/x")
    assert "52000" in p.compensation and "jährlich" in p.compensation
    got = salary.parse("", p.description, p.compensation)
    assert got and got["min"] == 52000 and got["max"] == 62000
    assert got["currency"] == "EUR" and got["period"] == "year"


def test_a_page_without_json_ld_is_skipped():
    assert instaffo.parse_job("<html><body>nichts</body></html>", "https://x/1") is None
    assert instaffo.parse_job("<script type=\"application/ld+json\">{bad json</script>",
                              "https://x/1") is None


class _Fetcher:
    """Serves each list page once; a repeated page means the end of the list."""

    def __init__(self, pages: int = 2):
        self.errors = []
        self.seen = []
        self.pages = pages

    def get(self, url, headers=None, host_delay=None):
        self.seen.append(url)
        if url.startswith(instaffo.LIST.format(page=1)):
            return 200, _cards()
        if self.pages >= 2:
            return 200, _cards()       # the same two jobs: nothing new
        return 200, _cards()


def test_fetch_pages_and_stops_when_nothing_is_new():
    f = _Fetcher(pages=2)
    got = instaffo.fetch({"max_pages": 8}, f)
    assert len(got) == 2, "the second page repeats the first, so the walk stops"
    assert len(f.seen) == 2, "one request per page, not one per job"


def test_fetch_respects_max_pages():
    f = _Fetcher(pages=2)
    got = instaffo.fetch({"max_pages": 1}, f)
    assert len(got) == 2
    assert len(f.seen) == 1, "max_pages is a page budget"


def test_a_dead_page_is_an_error_not_a_crash():
    class Dead(_Fetcher):
        def get(self, url, headers=None, host_delay=None):
            return 503, ""

    f = Dead()
    assert instaffo.fetch({"max_pages": 3}, f) == []
    assert f.errors and "HTTP 503" in f.errors[0]


def test_fetch_detail_returns_the_advert_text():
    class One(_Fetcher):
        def get(self, url, headers=None, host_delay=None):
            return 200, _detail()

    p = instaffo.parse_cards(_cards())[0]
    text = instaffo.fetch_detail(p, One())
    assert text and "IT-Architekturen" in text
