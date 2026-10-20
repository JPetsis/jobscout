"""Instaffo: the public job board behind app.instaffo.com.

The candidate app is `Disallow: /` in robots.txt, so the source reads only what
the site advertises to crawlers: sitemap-jobs.xml for discovery, and the
server-rendered job pages (each carrying a schema.org JobPosting) for content.

Fixtures are trimmed from the live site on 2026-10-20: a sitemap with two jobs
in both languages plus a category page, and one real job page with its JSON-LD.
"""
from pathlib import Path

from jobscout import salary
from jobscout.http import Fetcher
from jobscout.sources import instaffo

FIX = Path(__file__).parent / "fixtures"


def _detail() -> str:
    return (FIX / "instaffo_job.html").read_text(encoding="utf-8")


def _sitemap() -> str:
    return (FIX / "instaffo_sitemap.xml").read_text(encoding="utf-8")


def test_one_job_has_two_urls_and_counts_once():
    """The sitemap lists every role in German and English; the cap is per JOB."""
    jobs = instaffo.parse_sitemap(_sitemap())
    assert len(jobs) == 2, "two roles, not four URLs"
    ids = [j[1] for j in jobs]
    assert ids == ["junior-it-consultant-m-f-d-5b3d78bc1bd5",
                   "java-softwareentwickler-full-stack-m-w-d-08d2c6930c58"], "newest first"
    assert all("/de/job/" in j[2] for j in jobs), "the German page is canonical"
    # a category page is not a job
    assert all("/jobs/" not in j[2] for j in jobs)


def test_the_job_page_yields_a_posting():
    p = instaffo.parse_job(_detail(), "https://jobs.instaffo.com/de/job/x")
    assert p is not None
    assert p.source == "instaffo"
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
    """Serves the sitemap and one canned page per job URL."""

    def __init__(self):
        self.errors = []
        self.seen = []

    def get(self, url, headers=None, host_delay=None):
        self.seen.append(url)
        if url == instaffo.SITEMAP:
            return 200, _sitemap()
        return 200, _detail()


def test_fetch_walks_only_the_newest_max_jobs():
    f = _Fetcher()
    got = instaffo.fetch({"max_jobs": 1}, f)
    assert len(got) == 1, "the cap is a page budget, not a wish"
    assert got[0].title == "Junior IT Consultant (m/f/d)"
    # one sitemap request plus one page, not four
    assert len(f.seen) == 2


def test_fetch_reports_a_dead_sitemap():
    import pytest
    from jobscout.http import FetchError

    class Dead(_Fetcher):
        def get(self, url, headers=None, host_delay=None):
            return 503, ""

    with pytest.raises(FetchError):
        instaffo.fetch({"max_jobs": 5}, Dead())
