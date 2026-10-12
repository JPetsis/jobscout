"""Tests for the two keyed sources: adzuna and usajobs.

Neither API key exists on this machine, so `parse` is exercised against
HAND-WRITTEN fixtures built from the vendors' own documented response shapes
(Adzuna's official example response from developer.adzuna.com/docs/search and
USAJOBS' /api/Search reference response), and `fetch` is exercised against
scripted HTTP responses. The auth-failure behaviour is the live-probed part:
Adzuna answers HTTP 400 with an HTML body when NO key is sent and HTTP 401
{"exception": "AUTH_FAIL"} when a WRONG key is sent; USAJOBS answers HTTP 401
{"title": "Unauthorized"}.

The rule these tests lock in: a missing key must NEVER yield a silent empty
list — it must produce a loud, actionable error naming the config key and the
free signup URL.
"""
import json

from jobscout import salary
from jobscout.sources import REGISTRY, SOURCE_NAMES, adzuna, usajobs

# --- fixtures (hand-written from the documented shapes) ----------------------

# Adzuna, from https://developer.adzuna.com/docs/search — the official example
# response, with the dates moved forward and a third row for the single-bound
# salary case. Note the nesting: company/location are objects, salaries are
# plain numbers, and `description` is explicitly truncated by the API.
ADZUNA_SEARCH = {
    "__CLASS__": "Adzuna::API::Response::JobSearchResults",
    "results": [
        {
            "__CLASS__": "Adzuna::API::Response::Job",
            "id": "129698749",
            "title": "Javascript Developer",
            "created": "2026-09-18T18:07:39Z",
            "redirect_url": "https://www.adzuna.co.uk/jobs/land/ad/129698749"
                            "?v=712F911142DFEF191FB2F0E068CD2734177B7F7E"
                            "&utm_medium=api&utm_source=6eda9a47",
            "company": {"__CLASS__": "Adzuna::API::Response::Company",
                        "display_name": "Corporate Project Solutions"},
            "location": {"__CLASS__": "Adzuna::API::Response::Location",
                         "area": ["UK", "South East England",
                                  "Buckinghamshire", "Marlow"],
                         "display_name": "Marlow, Buckinghamshire"},
            "description": "JavaScript Developer Corporate <b>consultancy</b> "
                           "&amp; delivery team ... [truncated by Adzuna]",
            "salary_min": 50000,
            "salary_max": 55000,
            "salary_is_predicted": 0,
            "contract_type": "permanent",
            "category": {"__CLASS__": "Adzuna::API::Response::Category",
                         "label": "IT Jobs", "tag": "it-jobs"},
        },
        {
            "id": "130112233",
            "title": "Backend Engineer (Platform)",
            "created": "2026-09-20T09:12:00Z",
            "redirect_url": "https://www.adzuna.co.uk/jobs/land/ad/130112233",
            "company": {"display_name": "Acme Platforms Ltd"},
            "location": {"area": ["UK", "London"], "display_name": "London"},
            "description": "Build the platform.",
            "contract_time": "full_time",
            "contract_type": "permanent",
            "category": {"label": "IT Jobs", "tag": "it-jobs"},
        },
        {
            "id": "130445566",
            "title": "Data Analyst",
            "created": "2026-09-21T11:45:02Z",
            "redirect_url": "https://www.adzuna.co.uk/jobs/land/ad/130445566",
            "company": {"display_name": "Insight Partners Group"},
            "location": {"area": ["UK", "Manchester"],
                         "display_name": "Manchester"},
            "description": "Reporting analyst role.",
            "salary_min": 28000,
            "salary_max": None,
            "salary_is_predicted": 1,
            "category": {"label": "Accounting & Finance Jobs",
                         "tag": "accounting-finance-jobs"},
        },
    ],
}

# USAJOBS, built from the /api/Search reference response
# (developer.usajobs.gov/api-reference/get-api-search), keeping its exact field
# nesting: SearchResult.SearchResultItems[].MatchedObjectDescriptor, positions
# under UserArea.Details.JobSummary, pay under PositionRemuneration.
USAJOBS_SEARCH = {
    "LanguageCode": "EN",
    "SearchParameters": {},
    "SearchResult": {
        "SearchResultCount": 2,
        "SearchResultCountAll": 137,
        "SearchResultItems": [
            {
                "MatchedObjectId": "21947200",
                "MatchedObjectDescriptor": {
                    "PositionID": "SW62210-05-1716110PB411413H",
                    "PositionTitle": "IT SPECIALIST (INFOSEC/NETWORK)",
                    "PositionURI": "https://www.usajobs.gov/GetJob/ViewDetails/21947200",
                    "ApplyURI": ["https://www.usajobs.gov/GetJob/ViewDetails/"
                                 "21947200?PostingChannelID=RESTAPI"],
                    "PositionLocationDisplay":
                        "Point Loma Complex, San Diego, California",
                    "PositionLocation": [{
                        "LocationName": "Point Loma Complex, San Diego, California",
                        "CountryCode": "United States",
                        "CountrySubDivisionCode": "California",
                        "CityName": "San Diego, California",
                        "Longitude": -117.194382, "Latitude": 32.7042961,
                    }],
                    "OrganizationName": "Space and Naval Warfare Systems Command",
                    "DepartmentName": "Department of the Navy",
                    "JobCategory": [{"Name": "Information Technology", "Code": "2200"},
                                    {"Name": "Information Technology Management",
                                     "Code": "2210"}],
                    "JobGrade": [{"Code": "NO"}],
                    "PositionSchedule": [{"Name": "Full Time", "Code": "1"}],
                    "PositionOfferingType": [{"Name": "Permanent", "Code": "15317"}],
                    "PositionRemuneration": [{
                        "MinimumRange": "92108", "MaximumRange": "119746",
                        "RateIntervalCode": "PA", "Description": "Per Year",
                    }],
                    "PositionStartDate": "2026-07-23T00:00:00Z",
                    "PositionEndDate": "2026-08-06T00:00:00Z",
                    "UserArea": {
                        "Details": {
                            "JobSummary": "<p>You will serve as an <strong>IT "
                                          "SPECIALIST</strong> (INFOSEC/NETWORK) "
                                          "at the Point Loma Complex.</p>",
                            "LowGrade": "13", "HighGrade": "14",
                        },
                        "IsRadialSearch": True,
                    },
                },
            },
            {
                "MatchedObjectId": "789654321",
                "MatchedObjectDescriptor": {
                    "PositionID": "DE-1010-07-2026-001",
                    "PositionTitle": "GENERAL ENGINEER",
                    "PositionURI": "https://www.usajobs.gov/GetJob/ViewDetails/789654321",
                    "PositionLocationDisplay": "Multiple Locations",
                    "PositionLocation": [
                        {"LocationName": "Aberdeen Proving Ground, Maryland",
                         "CountryCode": "United States"},
                        {"LocationName": "Warren, Michigan",
                         "CountryCode": "United States"},
                    ],
                    "OrganizationName": "Army Tank-Automotive and Armaments Command",
                    "DepartmentName": "Department of the Army",
                    "PositionRemuneration": [{
                        "MinimumRange": "45", "MaximumRange": "55",
                        "RateIntervalCode": "PH", "Description": "Per Hour",
                    }],
                    "PositionStartDate": "2026-08-01T00:00:00Z",
                    "UserArea": {"Details": {
                        "JobSummary": "Serves as a General Engineer performing "
                                      "engineering duties.",
                    }},
                },
            },
        ],
    },
}


class _Fetcher:
    """Scripted stand-in for jobscout.http.Fetcher: records calls, no network."""

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.errors: list[str] = []

    def get(self, url, headers=None, host_delay=None):
        self.calls.append((url, dict(headers or {})))
        if not self.responses:
            raise RuntimeError(
                f"unexpected request without a scripted response: {url}")
        return self.responses.pop(0)


def test_the_two_keyed_sources_are_registered():
    for name, label in (("adzuna", "Adzuna"), ("usajobs", "USAJOBS")):
        assert name in SOURCE_NAMES
        assert REGISTRY[name].NAME == name
        assert REGISTRY[name].LABEL == label


# --- adzuna ------------------------------------------------------------------

def test_adzuna_parse_maps_the_documented_shape():
    postings = adzuna.parse(ADZUNA_SEARCH, "gb")
    assert len(postings) == 3
    p = postings[0]
    assert p.source == "adzuna"
    assert p.title == "Javascript Developer"
    assert p.company == "Corporate Project Solutions"
    assert p.location == "Marlow, Buckinghamshire"
    assert p.url.startswith("https://www.adzuna.co.uk/jobs/land/ad/129698749")
    assert p.external_id == "129698749"
    assert p.posted_at == "2026-09-18"
    assert p.compensation == "50000-55000 GBP"
    assert p.extra["category"] == "IT Jobs"
    assert p.extra["contract_type"] == "permanent"
    assert p.extra["salary_is_predicted"] == 0
    assert p.extra["area"] == ["UK", "South East England", "Buckinghamshire", "Marlow"]
    assert "JavaScript Developer" in p.description and "<" not in p.description
    # no salary -> no compensation; a single bound still yields pay
    assert postings[1].compensation == ""
    assert postings[1].extra["contract_time"] == "full_time"
    assert postings[2].compensation == "28000 GBP"


def test_adzuna_country_decides_the_currency():
    assert adzuna.parse(ADZUNA_SEARCH, "de")[0].compensation == "50000-55000 EUR"
    assert adzuna.parse(ADZUNA_SEARCH, "us")[0].compensation == "50000-55000 USD"
    # a country outside the table yields pay WITHOUT a guessed currency
    assert adzuna.parse(ADZUNA_SEARCH, "xx")[0].compensation == "50000-55000"


def test_adzuna_parse_survives_empty_or_missing_results():
    assert adzuna.parse({}, "us") == []
    assert adzuna.parse({"results": []}, "us") == []
    assert adzuna.parse({"results": None}, "us") == []
    # a junk row among good ones is skipped, not fatal
    got = adzuna.parse({"results": [None, {"title": "x"}]}, "us")
    assert len(got) == 1 and got[0].title == "x"


def test_adzuna_missing_key_is_loud_and_never_silent():
    f = _Fetcher()
    assert adzuna.fetch({}, f) == []
    assert f.calls == [], "must not hit the network without credentials"
    msg = f.errors[0]
    assert "sources.adzuna.app_id" in msg
    assert "sources.adzuna.app_key" in msg
    assert "https://developer.adzuna.com/signup" in msg

    # one key alone is still not enough, and the message says which is missing
    f2 = _Fetcher()
    assert adzuna.fetch({"app_id": "my-id"}, f2) == []
    assert f2.calls == []
    assert "missing app_key" in f2.errors[0]
    assert "sources.adzuna.app_key" in f2.errors[0]


def test_adzuna_rejected_key_401_is_distinct_from_missing_key_400():
    # Both observed live with curl: a request WITHOUT a key answers HTTP 400
    # with an HTML body; a request with a WRONG key answers HTTP 401 with
    # {"exception": "AUTH_FAIL"}. The two errors must not be conflated.
    f = _Fetcher([(401, '{"exception":"AUTH_FAIL","__CLASS__":'
                        '"Adzuna::API::Response::Exception",'
                        '"display":"Authorisation failed"}')])
    assert adzuna.fetch({"app_id": "deadbeef", "app_key": "0" * 32}, f) == []
    rejected = f.errors[0]
    assert "401" in rejected and "REJECTED" in rejected
    assert "sources.adzuna.app_id" in rejected
    assert "sources.adzuna.app_key" in rejected
    assert "https://developer.adzuna.com/signup" in rejected

    f2 = _Fetcher([(400, "<!DOCTYPE html><html><head><title>Uh oh, something "
                          "isn't right</title></head></html>")])
    assert adzuna.fetch({"app_id": "deadbeef", "app_key": "0" * 32}, f2) == []
    malformed = f2.errors[0]
    assert "400" in malformed and "MISSING or malformed" in malformed
    assert malformed != rejected


def test_adzuna_fetch_sends_the_country_path_and_query_words():
    f = _Fetcher([(200, json.dumps(ADZUNA_SEARCH))])
    got = adzuna.fetch({"app_id": "my-id", "app_key": "my-key", "country": "de",
                        "what": "python developer", "where": "berlin"}, f)
    assert len(got) == 3 and not f.errors
    url = f.calls[0][0]
    assert url.startswith("https://api.adzuna.com/v1/api/jobs/de/search/1?")
    for part in ("app_id=my-id", "app_key=my-key", "results_per_page=50",
                 "content-type=application%2Fjson",
                 "what=python+developer", "where=berlin"):
        assert part in url
    # the country segment also decides the currency of the parsed pay
    assert got[0].compensation == "50000-55000 EUR"

    # country defaults to 'us'
    f2 = _Fetcher([(200, json.dumps(ADZUNA_SEARCH))])
    adzuna.fetch({"app_id": "i", "app_key": "k"}, f2)
    assert "/jobs/us/search/1?" in f2.calls[0][0]


def test_adzuna_compensation_is_machine_readable():
    got = salary.parse("", "", adzuna.parse(ADZUNA_SEARCH, "gb")[0].compensation)
    assert got and got["currency"] == "GBP" and got["period"] == "year"
    assert (got["min"], got["max"]) == (50000, 55000)


# --- usajobs -----------------------------------------------------------------

def test_usajobs_parse_maps_the_documented_shape():
    postings = usajobs.parse(USAJOBS_SEARCH)
    assert len(postings) == 2
    p = postings[0]
    assert p.source == "usajobs"
    assert p.title == "IT SPECIALIST (INFOSEC/NETWORK)"
    assert p.company == "Space and Naval Warfare Systems Command"
    assert p.location == "Point Loma Complex, San Diego, California"
    assert p.url == "https://www.usajobs.gov/GetJob/ViewDetails/21947200"
    assert p.external_id == "21947200"
    assert p.posted_at == "2026-07-23"
    assert p.compensation == "92108-119746 USD per year"
    assert p.extra["department"] == "Department of the Navy"
    assert p.extra["position_id"] == "SW62210-05-1716110PB411413H"
    assert "IT SPECIALIST" in p.description and "<" not in p.description
    # every duty location is kept; hourly pay keeps its period
    assert postings[1].location == ("Aberdeen Proving Ground, Maryland, "
                                    "Warren, Michigan")
    assert postings[1].compensation == "45-55 USD per hour"


def test_usajobs_parse_survives_empty_or_missing_results():
    assert usajobs.parse({}) == []
    assert usajobs.parse({"SearchResult": {}}) == []
    assert usajobs.parse({"SearchResult": {"SearchResultItems": None}}) == []
    assert usajobs.parse({"SearchResult": {"SearchResultItems": []}}) == []
    # an incomplete item is skipped, not fatal
    got = usajobs.parse({"SearchResult": {"SearchResultItems": [
        {"MatchedObjectId": "1"},   # no descriptor
        None,
        {"MatchedObjectId": "2",
         "MatchedObjectDescriptor": {"PositionTitle": "X"}},
    ]}})
    assert len(got) == 1 and got[0].title == "X"


def test_usajobs_missing_key_or_email_is_loud_and_never_silent():
    f = _Fetcher()
    assert usajobs.fetch({}, f) == []
    assert f.calls == [], "must not hit the network without credentials"
    msg = f.errors[0]
    assert "sources.usajobs.key" in msg
    assert "sources.usajobs.email" in msg
    assert "https://developer.usajobs.gov/apirequest/" in msg

    # a key without the email is incomplete too: the email IS the User-Agent
    f2 = _Fetcher()
    assert usajobs.fetch({"key": "0123456789abcdef"}, f2) == []
    assert f2.calls == []
    assert "missing email" in f2.errors[0]
    assert "sources.usajobs.email" in f2.errors[0]


def test_usajobs_rejected_key_is_loud_and_actionable():
    # observed live: HTTP 401 {"title": "Unauthorized", "status": 401}
    f = _Fetcher([(401, json.dumps({
        "type": "https://tools.ietf.org/html/rfc9110#section-15.5.2",
        "title": "Unauthorized", "status": 401,
        "traceId": "00-1e09044ca978f687d871edce4ef69874-b0752850bddccab7-00"}))])
    assert usajobs.fetch({"key": "wrong", "email": "me@example.com"}, f) == []
    msg = f.errors[0]
    assert "401" in msg and "REJECTED" in msg
    assert "sources.usajobs.key" in msg
    assert "https://developer.usajobs.gov/apirequest/" in msg


def test_usajobs_fetch_sends_the_three_required_headers_and_parses():
    f = _Fetcher([(200, json.dumps(USAJOBS_SEARCH))])
    got = usajobs.fetch({"key": "my-key", "email": "me@example.com",
                         "keyword": "software engineer",
                         "results_per_page": 25}, f)
    assert len(got) == 2 and not f.errors
    url, headers = f.calls[0]
    assert url.startswith("https://data.usajobs.gov/api/search?")
    assert "Keyword=software+engineer" in url
    assert "ResultsPerPage=25" in url and "Page=1" in url
    assert headers["Host"] == "data.usajobs.gov"
    assert headers["User-Agent"] == "me@example.com"    # the UA IS the email
    assert headers["Authorization-Key"] == "my-key"

    # ResultsPerPage is clamped to the documented API maximum of 500
    f2 = _Fetcher([(200, json.dumps(USAJOBS_SEARCH))])
    usajobs.fetch({"key": "k", "email": "e@example.com",
                   "results_per_page": 5000}, f2)
    assert "ResultsPerPage=500" in f2.calls[0][0]


def test_usajobs_compensation_is_machine_readable():
    for i, period in enumerate(("year", "hour")):
        p = usajobs.parse(USAJOBS_SEARCH)[i]
        got = salary.parse("", "", p.compensation)
        assert got and got["currency"] == "USD" and got["period"] == period
