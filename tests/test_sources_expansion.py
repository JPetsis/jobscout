"""The 2026-10-15 source batch: germantechjobs, swissdevjobs, jobup,
jobscout24, jobware, talent, englishjobs, governmentjobs, jobicy, teamtailor,
join, wordpressjobs, pythonjobs, fossjobs.

Fixtures are real slices captured live on 2026-10-15 (endpoints and gotchas:
`.hermes/plans/2026-10-15_213959-jobscout-source-expansion.md`); fetch()
behaviour is exercised against scripted HTTP responses.
"""
import datetime as _dt
import json

from jobscout.sources import (REGISTRY, SOURCE_NAMES, englishjobs, fossjobs,
                              germantechjobs, governmentjobs, jobicy, jobscout24,
                              jobup, jobware, join, pythonjobs, swissdevjobs,
                              talent, teamtailor, wordpressjobs)

NEW_SOURCES = ["germantechjobs", "swissdevjobs", "jobup", "jobscout24", "jobware",
               "talent", "englishjobs", "governmentjobs", "jobicy", "teamtailor",
               "join", "wordpressjobs", "pythonjobs", "fossjobs"]


class FakeGet:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.errors = []

    def get(self, url, headers=None, host_delay=None):
        self.urls.append(url)
        return self.responses.pop(0) if self.responses else (404, "")


class FakeJson:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.errors = []

    def get_json(self, url, headers=None, host_delay=None):
        self.urls.append(url)
        return self.responses.pop(0) if self.responses else {}


def test_batch_registered():
    for name in NEW_SOURCES:
        assert name in SOURCE_NAMES
        assert REGISTRY[name].NAME == name
        assert callable(REGISTRY[name].fetch)


# --- feed sources ----------------------------------------------------------

def test_germantechjobs(fixture_text):
    ps = germantechjobs.parse(fixture_text("germantechjobs.xml"))
    assert len(ps) == 2
    p = ps[0]
    assert p.source == "germantechjobs"
    assert p.title == "IT System Engineer M365 Teams (m/w/d)"
    assert p.company == "CompuGroup Medical SE & Co. KGaA"
    assert p.url == ("https://germantechjobs.de/jobs/"
                     "CompuGroup-Medical-SE--Co-KGaA-IT-System-Engineer-M365-Teams-mwd")
    assert p.compensation == "40.000 - 70.000 €"
    assert p.posted_at == "2026-10-15"
    assert p.description


def test_germantechjobs_feed_error_is_loud():
    f = FakeGet([(503, "")])
    assert germantechjobs.fetch({}, f) == []
    assert any("germantechjobs: HTTP 503" in e for e in f.errors)


def test_swissdevjobs_shares_the_family_parser(fixture_text):
    ps = swissdevjobs.parse(fixture_text("swissdevjobs.xml"))
    assert len(ps) == 1
    p = ps[0]
    assert p.source == "swissdevjobs"
    assert "BACHMANN" in p.company
    assert p.url.startswith("https://swissdevjobs.ch/jobs/")
    assert p.compensation.startswith("CHF")


def test_wordpressjobs(fixture_text):
    ps = wordpressjobs.parse(fixture_text("wordpressjobs.xml"))
    assert len(ps) == 1
    p = ps[0]
    assert p.title.startswith("Remote WordPress")
    assert p.url.startswith("https://jobs.wordpress.net/job/")
    assert p.posted_at == "2026-10-13"
    assert p.description


def test_pythonjobs_splits_company_and_location(fixture_text):
    ps = pythonjobs.parse(fixture_text("pythonjobs.xml"))
    assert len(ps) == 2
    p = ps[0]
    assert p.title == "Software Engineer - Full Stack"
    assert p.company == "3Gimbals"
    assert p.location == "Remote, United States"


def test_fossjobs_strips_type_prefix(fixture_text):
    ps = fossjobs.parse(fixture_text("fossjobs.xml"))
    assert len(ps) == 2
    p = ps[0]
    assert p.title == "Product Specialist - Baserow"
    assert p.company == "Baserow"
    assert p.location == "Remote, Germany"


# --- API sources -----------------------------------------------------------

def test_jobup(fixture_json):
    ps = jobup.parse(fixture_json("jobup_search.json"))
    assert len(ps) == 2
    p = ps[0]
    assert p.title == "Software Engineer PHP (Médias)"
    assert p.company == "INFOMANIAK NETWORK SA"
    assert p.location == "Genève"
    assert p.url.startswith("https://www.jobup.ch/")
    assert p.posted_at == "2026-10-14"


def test_jobup_rows_clamped_to_api_limit():
    f = FakeJson([{"documents": []}, {"documents": []}])
    jobup.fetch({"queries": [{"keywords": "x", "rows": 60, "pages": 2}]}, f)
    assert len(f.urls) == 2
    assert all("rows=20" in u for u in f.urls)
    assert "start=20" in f.urls[1]


def test_jobicy(fixture_json):
    ps = jobicy.parse(fixture_json("jobicy.json"))
    assert len(ps) == 2
    p = ps[0]
    assert p.title == "Senior Technical Account Manager"
    assert p.company == "Recorded Future, Inc."
    assert p.location == "USA"
    assert p.url.startswith("https://jobicy.com/jobs/")
    assert p.compensation == "127500-191500 USD per year"
    assert p.posted_at == "2026-10-15"


def test_jobicy_cursor_paging():
    f = FakeJson([{"jobs": [], "hasMore": True, "nextCursor": "abc"},
                  {"jobs": [], "hasMore": False}])
    jobicy.fetch({"count": 10, "max_pages": 3}, f)
    assert len(f.urls) == 2
    assert "count=10" in f.urls[0]
    assert "cursor=abc" in f.urls[1]
    f2 = FakeJson([{"jobs": [], "hasMore": True, "nextCursor": "a"},
                   {"jobs": [], "hasMore": True, "nextCursor": "b"},
                   {"jobs": [], "hasMore": True, "nextCursor": "c"}])
    jobicy.fetch({"max_pages": 2}, f2)
    assert len(f2.urls) == 2  # the page budget stops it, not the feed


# --- SSR sources -----------------------------------------------------------

def test_jobware_reads_data_attributes(fixture_text):
    html = fixture_text("jobware_list.html")
    # The trap this parser exists for: the cards carry NO plain anchors.
    assert 'href="/job/' not in html
    ps = jobware.parse(html)
    assert len(ps) == 1
    p = ps[0]
    assert p.title == "PHP Entwickler (m/w/d)"
    assert p.company == "Multa Medio Informationssysteme AG"
    assert p.location == "Würzburg (DE)"
    assert p.url == "https://www.jobware.de/job/php-entwickler-m-w-d-064559047"
    assert p.external_id == "064559047"
    assert p.posted_at == "2026-10-10"


def test_jobware_paging_and_location_warning(fixture_text):
    html = fixture_text("jobware_list.html")
    f = FakeGet([(200, html), (200, html)])
    jobware.fetch({"queries": [{"keywords": "php", "pages": 2}]}, f)
    assert f.urls[0].endswith("q=php")
    assert "&page=2" in f.urls[1]
    f2 = FakeGet([(200, html)])
    jobware.fetch({"queries": [{"keywords": "php", "location": "Berlin"}]}, f2)
    assert any("location filter not supported" in e for e in f2.errors)


def test_jobscout24(fixture_text):
    ps = jobscout24.parse(fixture_text("jobscout24_list.html"))
    assert len(ps) == 1
    p = ps[0]
    assert p.title == "Full Stack Developer (60-80%)"
    assert p.company == "Avenir Services AG"
    assert p.location == "Zürich"
    assert p.url == ("https://www.jobscout24.ch/de/job/"
                     "256dfaf2-af53-4c39-b203-6786f8fbc7be/")
    assert p.posted_at == (_dt.date.today() - _dt.timedelta(days=7)).isoformat()


def test_jobscout24_age_helper():
    today = _dt.date.today()
    assert jobscout24._age_to_date("2 T") == (today - _dt.timedelta(days=2)).isoformat()
    assert jobscout24._age_to_date("heute") == today.isoformat()
    assert jobscout24._age_to_date("") == ""
    assert jobscout24._age_to_date("garbage") == ""


def test_jobscout24_pager_is_p_not_page(fixture_text):
    html = fixture_text("jobscout24_list.html")
    f = FakeGet([(200, html), (200, html)])
    jobscout24.fetch({"queries": [{"keywords": "php", "pages": 2}]}, f)
    assert "SearchWhat=php" in f.urls[0]
    assert f.urls[1].endswith("&p=2")
    assert "page=2" not in f.urls[1]


def test_governmentjobs(fixture_text):
    ps = governmentjobs.parse(fixture_text("governmentjobs_list.html"))
    assert len(ps) == 1
    p = ps[0]
    assert p.title == "Systems Engineer II"
    assert p.company == "City of Modesto"
    assert p.location == "Modesto, CA"
    assert "$91,083.20" in p.compensation
    assert p.url == "https://www.governmentjobs.com/jobs/5516467-0/systems-engineer-ii"
    assert p.external_id == "5516467-0"


def test_talent(fixture_text):
    ps = talent.parse(fixture_text("talent_list.html"))
    assert len(ps) == 1
    p = ps[0]
    assert p.title == "PHP Entwickler (m/w/d) - HR MasterData"
    assert p.company == "PerZukunft Arbeitsvermittlung GmbH&Co.KG"
    assert p.location == "Berlin, Deutschland"
    assert p.url == "https://de.talent.com/view?id=534434318532478950"
    assert p.posted_at == "2026-10-08"


def test_englishjobs(fixture_text):
    ps = englishjobs.parse(fixture_text("englishjobs_list.html"))
    assert len(ps) == 1
    p = ps[0]
    assert p.title == "VP of Sales - Defence (m/f/d)"
    assert p.company == "HAPEKO Deutschland GmbH"
    assert p.location == "München"
    assert p.url.startswith("https://englishjobs.de/clickout/")
    assert "&amp;" not in p.url
    assert p.external_id == "c029372a99e0bee8"


def test_englishjobs_fetch_pages(fixture_text):
    html = fixture_text("englishjobs_list.html")
    f = FakeGet([(200, html), (200, html)])
    englishjobs.fetch({"categories": ["remote"], "pages": 2}, f)
    assert f.urls[0].endswith("/jobs/remote")
    assert f.urls[1].endswith("/jobs/remote?page=2")


def test_join(fixture_text):
    ps = join.parse(fixture_text("join_board.html"), "join")
    assert len(ps) == 1
    p = ps[0]
    assert p.title == "Senior Growth Product Marketing Manager"
    assert p.company == "JOIN"
    assert p.location == "Barcelona, Spain (hybrid)"
    assert p.url == ("https://join.com/companies/join/"
                     "16805344-senior-growth-product-marketing-manager")
    assert p.external_id == "16805344"


def test_join_slug_parsing():
    assert join._slug_for("join") == "join"
    assert join._slug_for("https://join.com/companies/acme/jobs") == "acme"
    assert join._slug_for("https://join.com/companies/acme") == "acme"
    assert join._slug_for("") == ""


def test_teamtailor_feed(fixture_json):
    ps = teamtailor.parse_feed(fixture_json("teamtailor.json"),
                               "teamviewer.teamtailor.com")
    assert len(ps) == 2
    p = ps[0]
    assert p.company == "TeamViewer Germany GmbH"
    assert "Chicago" in p.location
    assert p.url.startswith("https://teamviewer.teamtailor.com/jobs/")
    assert p.posted_at == "2026-10-15"
    assert p.description


def test_teamtailor_follows_next_url():
    page1 = {"items": [], "next_url": "https://tv.teamtailor.com/jobs.json?page=2"}
    page2 = {"items": []}
    f = FakeGet([(200, json.dumps(page1)), (200, json.dumps(page2))])
    teamtailor.fetch({"companies": ["teamviewer"], "max_pages": 2}, f)
    assert f.urls[0] == "https://teamviewer.teamtailor.com/jobs.json"
    assert f.urls[1] == page1["next_url"]
    f1 = FakeGet([(200, json.dumps(page1))])
    teamtailor.fetch({"companies": ["teamviewer"], "max_pages": 1}, f1)
    assert len(f1.urls) == 1
    f2 = FakeGet([(404, "")])
    teamtailor.fetch({"companies": ["nope"]}, f2)
    assert any("teamtailor/nope.teamtailor.com: HTTP 404" in e for e in f2.errors)


def test_teamtailor_host_for():
    assert teamtailor._host_for("teamviewer") == "teamviewer.teamtailor.com"
    assert (teamtailor._host_for("https://teamviewer.teamtailor.com/jobs")
            == "teamviewer.teamtailor.com")
    assert teamtailor._host_for("") == ""


# --- querygen --------------------------------------------------------------

def test_new_query_driven_sources_get_generated_queries():
    from types import SimpleNamespace
    from jobscout import querygen
    cfg = SimpleNamespace(sources_cfg={},
                          prefs={"title_terms": ["entwickler"],
                                 "stack_preferred": {"php": 3}})
    kws = [q["keywords"] for q in querygen.generate_queries(cfg, "jobware")]
    assert "" in kws          # the broad sweep comes first
    assert "entwickler" in kws
    assert "php" in kws
    assert querygen.generate_queries(cfg, "germantechjobs") == []
