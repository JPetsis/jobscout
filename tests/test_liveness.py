from jobscout import liveness
from jobscout.http import FetchError


class FakeFetcher:
    def __init__(self, response):
        self.response = response
        self.seen = []

    def get(self, url, headers=None):
        self.seen.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class RouteFetcher:
    """Routes by URL substring; unmatched URLs raise, so a test fails loudly."""

    def __init__(self, routes):
        self.routes = routes
        self.seen = []
        self.headers = []

    def get(self, url, headers=None):
        self.seen.append(url)
        if headers:
            self.headers.append(headers)
        for frag, response in self.routes.items():
            if frag in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected fetch: {url}")


def check(response, url="https://x/1"):
    return liveness.check(FakeFetcher(response), url)


def test_empty_url_is_unknown():
    assert check(None, url="") == ("unknown", "no url")


def test_404_and_410_are_dead():
    assert check((404, ""))[0] == "dead"
    assert check((410, "")) == ("dead", "HTTP 410")


def test_larajobs_uses_the_open_api_not_the_challenged_page():
    # larajobs.com/job/<id> answers 401 (Cloudflare), but /api/jobs is open and
    # lists every live posting by id, so membership is a real verdict.
    url = "https://larajobs.com/job/3937"
    live = RouteFetcher({"/api/jobs": (200, '[{"id": 3937, "title": "PHP/Laravel"}]')})
    assert liveness.check(live, url) == ("ok", "")

    gone = RouteFetcher({"/api/jobs": (200, '[{"id": 111, "title": "Other"}]')})
    assert liveness.check(gone, url) == ("dead", "not in the larajobs feed anymore")

    # a blocked or empty API must stay unknown, never dead
    assert liveness.check(RouteFetcher({"/api/jobs": (401, "")}), url)[0] == "unknown"
    assert liveness.check(RouteFetcher({"/api/jobs": (200, "[]")}), url)[0] == "unknown"
    assert liveness.check(RouteFetcher({"/api/jobs": (200, "not json")}), url)[0] == "unknown"


def test_challenge_hosts_are_marked_unverifiable_not_merely_blocked():
    # himalayas serves a public API but puts every posting page behind a
    # Cloudflare Turnstile challenge, so a 403 there is structural: re-probing
    # can never succeed. The note must say so, and must NOT leak to other
    # hosts, where 403 still means "maybe a soft 404".
    u = "https://himalayas.app/companies/acme/jobs/dev"
    state, note = check((403, ""), url=u)
    assert state == "unknown"
    assert "challenge" in note and "unverifiable" in note
    assert check((403, "")) == ("unknown", "HTTP 403 (blocked?)")
    # a real 404/200 on the same host still decides normally
    assert check((404, ""), url=u) == ("dead", "HTTP 404")
    assert check((200, "Senior Developer"), url=u) == ("ok", "")


def test_soft_statuses_are_never_dead():
    # a blocked request is not evidence a posting is gone
    for status in (401, 403, 429):
        assert check((status, ""))[0] == "unknown"


def test_publisher_expiry_decides_without_any_fetch():
    # This is the himalayas path: the URL is permanently 403, so the only way
    # to get a verdict is from a date the publisher itself published.
    import datetime
    past = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    u = "https://himalayas.app/companies/acme/jobs/dev"

    f = FakeFetcher((403, ""))           # would be unknown on its own
    assert liveness.check(f, u, past) == ("dead", f"publisher expiry passed ({past})")
    assert f.seen == [], "expiry must decide WITHOUT touching the network"

    # a FUTURE expiry proves the posting existed, NOT that it lives today, so
    # it must not claim "ok". (A 14-day "trust the near future" window was tried
    # and reverted: himalayas stamps a flat 60-day expiry, so it would have
    # accepted none of the 404 real rows while looking clever.)
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    f2 = FakeFetcher((403, ""))
    assert liveness.check(f2, u, future)[0] == "unknown" and f2.seen, \
        "a future expiry must not certify liveness"

    # garbage falls through to the normal probe instead of guessing
    f3 = FakeFetcher((200, "Senior Developer"))
    assert liveness.check(f3, u, "not-a-date") == ("ok", "")

    # today is not yet expired
    today = datetime.date.today().isoformat()
    f4 = FakeFetcher((403, ""))
    assert liveness.check(f4, u, today)[0] == "unknown"

    # Regression: an EMPTY expiry must never expire anything. "" sorts before
    # every real date, so a string comparison would kill all 800 himalayas
    # rows — most of which have no expiry stored at all.
    f5 = FakeFetcher((403, ""))
    assert liveness.check(f5, u, "")[0] == "unknown"
    f6 = FakeFetcher((403, ""))
    assert liveness.check(f6, u, "   ")[0] == "unknown"


def test_gone_phrases_are_dead():
    assert check((200, "Sorry, we are no longer accepting applications"))[0] == "dead"
    assert check((200, "This position has been filled."))[0] == "dead"
    assert check((200, "Diese Stelle ist nicht mehr verfügbar"))[0] == "dead"
    assert check((200, "No such item."))[0] == "dead"


def test_normal_page_is_ok():
    assert check((200, "<h1>Senior Python Engineer</h1> apply now")) == ("ok", "")


def test_blocked_and_server_errors_are_unknown():
    assert check((403, "")) == ("unknown", "HTTP 403 (blocked?)")
    assert check((429, ""))[0] == "unknown"
    assert check((500, ""))[0] == "unknown"


def test_fetch_error_is_unknown():
    state, note = check(FetchError("https://x/1", "boom"))
    assert state == "unknown" and "fetch error" in note


def test_phrases_do_not_match_healthy_pages():
    # "this job is great" must not trip the markers
    assert check((200, "this job is great and closed captions are available"))[0] == "ok"


# --- ATS-aware probes (board pages answer 200 for anything) -----------------

def test_greenhouse_job_uses_the_api():
    url = "https://job-boards.greenhouse.io/celonis/jobs/7887061003"
    live = RouteFetcher({"/boards/celonis/jobs/7887061003": (200, "{}")})
    assert liveness.check(live, url) == ("ok", "")
    gone = RouteFetcher({"/boards/celonis/jobs/1": (404, "")})
    state, note = liveness.check(gone, "https://job-boards.greenhouse.io/celonis/jobs/1")
    assert state == "dead" and "gone" in note
    # the SPA web page is never fetched
    assert all("api" in u for u in gone.seen)


def test_lever_job_uses_the_api():
    url = "https://jobs.lever.co/leverdemo/11111111-2222-3333-4444-555555555555"
    gone = RouteFetcher({"/v0/postings/leverdemo/": (404, "")})
    assert liveness.check(gone, url)[0] == "dead"
    live = RouteFetcher({"/v0/postings/leverdemo/": (200, "{}")})
    assert liveness.check(live, url) == ("ok", "")


def test_ashby_job_checks_the_board_listing():
    uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    url = f"https://jobs.ashbyhq.com/n8n/{uuid}"
    gone = RouteFetcher({"posting-api/job-board/n8n":
                         (200, '{"jobs": [{"id": "99999999-9999-9999-9999-999999999999"}]}')})
    state, note = liveness.check(gone, url)
    assert state == "dead" and "board" in note
    live = RouteFetcher({"posting-api/job-board/n8n": (200, f'{{"jobs": [{{"id": "{uuid}"}}]}}')})
    assert liveness.check(live, url) == ("ok", "")
    empty = RouteFetcher({"posting-api/job-board/n8n": (200, '{"jobs": []}')})
    assert liveness.check(empty, url)[0] == "unknown"  # cannot claim dead from an empty board


def test_smartrecruiters_job_uses_the_api():
    url = "https://jobs.smartrecruiters.com/Visa/744000099999999"
    gone = RouteFetcher({"/companies/Visa/postings/": (404, "")})
    assert liveness.check(gone, url)[0] == "dead"


def test_eures_job_uses_the_api_not_the_portal_page():
    # the portal page answers 200 for any id, so only the API can prove death
    url = "https://europa.eu/eures/portal/jv/id/MTUxNTgtMjAyNjI4MzUwOS1TIDE.aspx?lang=en"
    gone = RouteFetcher({"/jv/id/": (404, "")})
    state, note = liveness.check(gone, url)
    assert state == "dead" and "gone" in note
    assert all("portal" not in u for u in gone.seen)   # never fetched the page

    live = RouteFetcher({"/jv/id/": (200, '{"id": "MTUxNTgt", "jvProfiles": [{"a": 1}]}')})
    assert liveness.check(live, url) == ("ok", "")

    empty = RouteFetcher({"/jv/id/": (200, "{}")})
    assert liveness.check(empty, url)[0] == "dead"

    blocked = RouteFetcher({"/jv/id/": (429, "")})
    assert liveness.check(blocked, url)[0] == "unknown"   # blocked is not dead


def test_ams_job_uses_the_offer_api_not_the_spa_page():
    # every jobs.ams.at path returns 200 with the same SPA shell, so the page
    # can never prove death; /joboffer/uuid/<uuid> is the authoritative check
    url = "https://jobs.ams.at/public/emps/jobs/1702834a-7ce5-3ac3-bea5-e5870d5be10e"
    gone = RouteFetcher({"/joboffer/uuid/": (404, "")})
    state, note = liveness.check(gone, url)
    assert state == "dead" and "gone" in note
    assert all("/public/emps/jobs/" not in u for u in gone.seen)   # never fetched the page
    # the API needs the three signed headers, like the search endpoint
    assert gone.headers and "x-ams-access-token" in gone.headers[-1]
    assert len(gone.headers[-1]["x-ams-access-token"]) == 128     # HMAC-SHA512

    live = RouteFetcher({"/joboffer/uuid/": (200, '{"id": 22875584}')})
    assert liveness.check(live, url) == ("ok", "")

    # a 401/500 must never be reported as dead
    auth = RouteFetcher({"/joboffer/uuid/": (401, "")})
    assert liveness.check(auth, url)[0] == "unknown"
    down = RouteFetcher({"/joboffer/uuid/": FetchError(url, "boom")})
    assert liveness.check(down, url)[0] == "unknown"


def test_old_ams_jobdetails_url_is_not_treated_as_a_job():
    # the retired /jobDetails?jobId= route has no uuid, so no targeted probe
    # applies and it falls through to the generic page check
    assert liveness._ats_check(RouteFetcher({}), "https://jobs.ams.at/public/emps/jobDetails?jobId=22875584") is None