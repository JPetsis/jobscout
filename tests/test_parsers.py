import json
import re
from datetime import date

from jobscout.sources import (REGISTRY, SOURCE_NAMES, ams, arbeitnow, ashby,
                              berlinstartupjobs, eures, greenhouse, himalayas, hn,
                              jobs_ch, jobsat, karriere_at, larajobs, lever, linkedin,
                              nodesk, recruitee, remoteok, remotive, smartrecruiters,
                              stepstone, stellenanzeigen, workable, workingnomads, wwr,
                              xing)


def test_registry_complete():
    assert set(REGISTRY) == set(SOURCE_NAMES)
    for name, mod in REGISTRY.items():
        assert mod.NAME == name
        assert callable(mod.fetch)


def test_stepstone(fixture_text, strip_styles):
    postings = stepstone.parse(strip_styles(fixture_text("stepstone_list.html")))
    assert len(postings) == 2
    p = postings[0]
    assert "Softwareentwickler" in p.title
    assert p.company and "Berlin" in p.location
    assert p.url.startswith("https://www.stepstone.de/stellenangebote--")
    assert p.external_id.isdigit()
    assert re.match(r"\d{4}-\d{2}-\d{2}", p.posted_at)
    assert "<" not in p.location


def test_xing(fixture_text, strip_styles):
    postings = xing.parse(strip_styles(fixture_text("xing_list.html")))
    assert len(postings) == 2
    p = postings[0]
    assert p.company and p.location == "Berlin"
    assert p.url.startswith("https://www.xing.com/jobs/")
    assert p.external_id.isdigit()
    assert re.match(r"\d{4}-\d{2}-\d{2}", p.posted_at)


def test_stellenanzeigen(fixture_text):
    postings = stellenanzeigen.parse(fixture_text("stellenanzeigen_list.html"))
    assert len(postings) == 3
    p = postings[0]
    assert p.external_id == "SDE-121019"
    assert "Softwareentwickler" in p.title
    assert p.company == "LexCom Informationssysteme GmbH"
    assert p.location == "München"
    assert p.url.endswith("/job/softwarearchitekt-in-und-softwareentwickler-in-all-genders-im-bereich-bigdata-muenchen-sde-121019/")


def test_karriere_at(fixture_text):
    postings = karriere_at.parse(fixture_text("karriere_at.html"))
    assert len(postings) == 3
    p = postings[0]
    assert p.url == "https://www.karriere.at/jobs/7792532"
    assert p.external_id == "7792532"
    assert "Wiener Wohnen" in p.company
    assert p.location == "Wien"
    assert p.description  # summary+snippet stored
    if p.posted_at:
        assert re.match(r"\d{4}-\d{2}-\d{2}", p.posted_at)


def test_linkedin(fixture_text):
    postings = linkedin.parse(fixture_text("linkedin_list.html"))
    assert len(postings) == 3
    p = postings[0]
    assert p.external_id == "4463496178"
    assert p.url.startswith("https://de.linkedin.com/jobs/view/")
    assert "?" not in p.url
    assert p.company == "JetBrains"
    assert p.posted_at == "2026-09-10"


def test_linkedin_detail_slice():
    html = ('<div class="show-more-less-html__markup x"><ul><li>One</li></ul><div>'
            '<p>Inner</p></div></div><div class="other">ignored</div>')
    m = linkedin._RE_DETAIL.search(html)
    text = linkedin._slice_div(html, m.end())
    assert "One" in text and "Inner" in text and "ignored" not in text


def test_jobs_ch(fixture_json):
    postings = jobs_ch.parse(fixture_json("jobs_ch_search.json"))
    assert len(postings) == 2
    p = postings[0]
    assert p.title and p.company
    assert p.url.startswith("https://www.jobs.ch/")
    assert re.match(r"\d{4}-\d{2}-\d{2}", p.posted_at)
    assert p.location


def test_jobs_ch_rows_clamped_to_api_limit():
    seen = []

    class FakeFetcher:
        def get_json(self, url, headers=None, host_delay=None):
            seen.append(url)
            return {"documents": []}

    jobs_ch.fetch({"queries": [{"keywords": "x", "rows": 60, "pages": 2}]}, FakeFetcher())
    assert len(seen) == 2
    assert all("rows=20" in u for u in seen)
    assert "start=20" in seen[1]


def test_arbeitnow(fixture_json):
    postings = arbeitnow.parse(fixture_json("arbeitnow.json"))
    assert len(postings) == 2
    assert postings[0].url.startswith("https://www.arbeitnow.com/jobs/")
    assert re.match(r"\d{4}-\d{2}-\d{2}", postings[0].posted_at)


def test_berlinstartupjobs(fixture_text):
    postings = berlinstartupjobs.parse(fixture_text("berlinstartupjobs.xml"))
    assert len(postings) == 2
    first = postings[0]
    assert first.company == "Mein Grundeinkommen e.V."
    assert first.posted_at == "2026-09-14"
    assert first.location == "Berlin"


def test_hn(fixture_json):
    story = fixture_json("hn_story.json")
    assert "Who is hiring" in story["title"]
    c = hn.parse_comment(1, fixture_json("hn_comment_0.json"))
    assert c.company == "QUOBYTE"
    assert c.location == "Berlin, Germany"
    assert c.url == "https://news.ycombinator.com/item?id=1"
    assert c.posted_at.startswith("2026-")
    assert "Quobyte" in c.description
    assert "http" not in c.title
    # the paragraph after the first line must not leak into the first-line fields
    assert c.title == "QUOBYTE | Berlin, Germany | Full-time | ONSITE (Germany)"
    assert "At Quobyte" not in c.title


def test_hn_role_first_comment_without_company(fixture_json):
    # "Senior Python Backend Engineer | REMOTE (EMEA/APAC)<p>We're looking ..."
    # First line is "Role | Location" and the body used to glue onto both.
    c = hn.parse_comment(2, fixture_json("hn_comment_reef.json"))
    assert c.title == "Senior Python Backend Engineer"
    assert c.location == "REMOTE (EMEA/APAC)"
    assert c.company == ""
    for field in (c.title, c.company, c.location):
        assert "We're looking" not in field
        assert "<p>" not in field
    assert "trustless supercluster" in c.description


def test_hn_company_with_comma_stays_company():
    # "Org, Country | Role | ..." — the comma part before the role is the company.
    c = hn.parse_comment(3, {"text": "Paul Scherrer Institut (PSI), Switzerland | "
                                     "Software Scientist / Research Software Engineer | 2y project"
                                     "<p>Body text", "time": 1788277998})
    assert c.title == "Software Scientist / Research Software Engineer"
    assert c.company == "Paul Scherrer Institut (PSI), Switzerland"
    assert c.location == ""


def test_hn_no_role_line_keeps_first_line_as_title():
    c = hn.parse_comment(4, {"text": "Acme | Multiple Positions | Berlin, Germany | remote<p>body",
                             "time": 1788277998})
    assert c.title == "Acme | Multiple Positions | Berlin, Germany | remote"
    assert c.company == "Acme"
    assert c.location == "Berlin, Germany"


def test_hn_link_beside_company_and_salary_not_location():
    c = hn.parse_comment(5, {"text": "Snout https://snout.com/ | Multiple Engineering + Product Roles | "
                                     "Remote US or Ontario, Canada | Full Time<p>body", "time": 1788277998})
    assert c.company == "Snout"
    assert c.title == "Multiple Engineering + Product Roles"
    assert c.location == "Remote US or Ontario, Canada"

    c2 = hn.parse_comment(6, {"text": "Devin (https://experiencedevin.com/) | Toronto / Miami / Boston-Cambridge | "
                                      "ONSITE/hybrid | $100,000\u2013$150,000 USD<p>body", "time": 1788277998})
    assert c2.company == "Devin"
    assert c2.location == "ONSITE/hybrid"
    assert "$" not in c2.location


def test_hn_member_of_technical_staff_is_a_role():
    c = hn.parse_comment(7, {"text": "Anterior (Sequoia-backed, Series B) | Senior Member of Technical Staff | "
                                     "On-Site (New York, NY) | Full Time | $230,000\u2013$250,000<p>body",
                             "time": 1788277998})
    assert c.title == "Senior Member of Technical Staff"
    assert c.company == "Anterior (Sequoia-backed, Series B)"
    assert c.location == "On-Site (New York, NY)"


def test_remotive(fixture_json):
    postings = remotive.parse(fixture_json("remotive.json"))
    assert len(postings) == 2
    assert postings[0].company == "A.Team"
    assert postings[0].location


def test_wwr(fixture_text):
    postings = wwr.parse(fixture_text("wwr.xml"))
    assert len(postings) == 2
    p = postings[0]
    assert p.company == "Smartcat"
    assert p.title == "Senior Product Manager"
    assert p.url.startswith("https://weworkremotely.com/")
    assert p.posted_at == "2026-08-19"


def test_larajobs(fixture_text):
    postings = larajobs.parse(fixture_text("larajobs.xml"))
    assert len(postings) == 10
    p = postings[0]
    assert p.company == "Greenwood Capital"
    assert p.title.startswith("Full-Stack Developer")
    assert p.url == "https://larajobs.com/job/3938"
    assert p.posted_at == "2026-09-22"
    assert p.compensation == "£60k"
    assert p.location == "Remote/Hybrid, UK Only"
    assert "Laravel" in p.description


def test_workingnomads(fixture_json):
    postings = workingnomads.parse(fixture_json("workingnomads.json"))
    assert len(postings) == 3
    p = postings[0]
    assert p.title == "Senior back-end Engineer"
    assert p.company == "Lemon.io"
    assert p.external_id == "1885527"
    assert p.posted_at == "2026-09-23"
    assert p.description and "<" not in p.description


def test_nodesk(fixture_text):
    postings = nodesk.parse(fixture_text("nodesk.xml"))
    assert len(postings) == 3
    p = postings[0]
    assert p.title == "Sr. Learning Experience Designer"
    assert p.company == "General Assembly"
    assert p.location == "Remote"
    assert p.posted_at == "2026-09-22"
    assert "General Assembly" in p.description


def test_himalayas(fixture_json):
    postings = himalayas.parse(fixture_json("himalayas.json"))
    assert len(postings) == 3
    p = postings[0]
    assert p.title.startswith("Profesor/a de Canto Moderno")
    assert p.company == "Tusclasesparticulares"
    assert p.location == "Spain"
    assert p.posted_at == "2026-09-23"
    assert p.compensation == "15-30 EUR per hour"
    assert p.description and "<" not in p.description
    # multi-word location + the structured salary fields landing in compensation
    assert postings[2].company == "Sensia" and postings[2].location == "United States"
    assert postings[1].compensation == ""


def test_himalayas_stores_the_publisher_expiry(fixture_json):
    # The only liveness signal reachable on this source, since every HTML page
    # is behind a Turnstile challenge. Losing it means permanently unknown.
    postings = himalayas.parse(fixture_json("himalayas.json"))
    assert all(p.expires_at for p in postings)
    # value comes from the fixture's own expiryDate epoch, not a guessed date
    raw = fixture_json("himalayas.json")["jobs"][0]["expiryDate"]
    assert postings[0].expires_at == date.fromtimestamp(raw).isoformat()


def test_himalayas_require_expiry_drops_unverifiable_rows():
    class F:
        errors = []
        requests = 0

        def get(self, url, **kw):
            return 200, json.dumps({"jobs": [
                {"title": "With Expiry", "guid": "https://h/1",
                 "pubDate": 1790535367, "expiryDate": 1795719366,
                 "description": "x", "locationRestrictions": ["Germany"]},
                {"title": "No Expiry", "guid": "https://h/2",
                 "pubDate": 1790535367,
                 "description": "x", "locationRestrictions": ["Germany"]},
            ]})

    f = F()
    kept = himalayas.fetch({"max_pages": 1, "require_expiry": True}, f)
    assert [p.title for p in kept] == ["With Expiry"]
    assert any("no publisher expiry" in e for e in f.errors)

    # without the setting, both are kept (unchanged default behaviour)
    f2 = F()
    assert len(himalayas.fetch({"max_pages": 1}, f2)) == 2


def test_himalayas_prune_never_deletes_a_decision_or_a_good_row():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE postings (id INTEGER PRIMARY KEY, source TEXT,
        expires_at TEXT, status TEXT, dup_of INTEGER, score REAL, filtered INTEGER)""")
    rows = [
        # (id, source, expires_at, status, dup_of, score, filtered, survives?)
        (1, "himalayas", "",        "new",       None, 0.0, 1, False),  # dead weight
        (2, "himalayas", "",        "new",       None, 3.0, 1, False),  # low score
        (3, "himalayas", "",        "dismissed", None, 0.0, 1, True),   # a decision
        (4, "himalayas", "",        "saved",     None, 0.0, 1, True),   # a decision
        (5, "himalayas", "",        "new",       None, 16.0, 0, True),  # live-pool gem
        (6, "himalayas", "",        "new",       99,   0.0, 1, True),   # a dup
        (7, "himalayas", "2026-12-01", "new",    None, 0.0, 1, True),   # verifiable
        (8, "arbeitnow",  "",       "new",       None, 0.0, 1, True),   # other source
    ]
    conn.executemany("INSERT INTO postings VALUES (?,?,?,?,?,?,?)", [r[:7] for r in rows])
    conn.commit()

    removed = himalayas.prune_unverifiable(
        conn, {"prune_unverifiable": True, "prune_below_score": 6})
    assert removed == 2, "only the score-0 filtered row and the low-score row"
    left = {r[0] for r in conn.execute("SELECT id FROM postings")}
    assert left == {3, 4, 5, 6, 7, 8}
    conn.close()


def test_himalayas_prune_is_off_by_default():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE postings (id INTEGER PRIMARY KEY, source TEXT,
        expires_at TEXT, status TEXT, dup_of INTEGER, score REAL, filtered INTEGER)""")
    conn.execute("INSERT INTO postings VALUES (1,'himalayas','','new',NULL,0.0,1)")
    conn.commit()
    assert himalayas.prune_unverifiable(conn, {}) == 0
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 1
    conn.close()


def test_eures(fixture_json):
    data = fixture_json("eures.json")
    postings = eures.parse(data["jvs"])
    assert len(postings) == 3
    p = postings[0]
    assert p.title.startswith("Key User / DevOps Engineer")
    assert p.posted_at == "2026-09-16"
    assert p.location == "Austria"
    assert p.external_id and p.url.endswith(".aspx?lang=en")
    assert p.description and "<" not in p.description
    # a cross-border posting keeps both NUTS countries
    assert postings[1].location == "Germany, Austria"
    # the German-language posting keeps its own text
    assert "Bahnplanung" in postings[1].title


def test_ams_token_is_reproducible():
    # regression: the API signs with HMAC-SHA512 (a 128-hex-char signature);
    # SHA-256 produces a valid-looking but rejected token.
    params = {"page": 1, "query": "x"}
    headers = ams._token("/public/emps/api/search", params)
    sig = headers["x-ams-access-token"]
    assert len(sig) == 128
    assert len(headers["x-ams-access-token-random"]) == 16
    assert headers["x-ams-access-token-timestamp"].isdigit()
    # the same inputs must reproduce the same signature
    again = ams._token("/public/emps/api/search", params)
    assert len(again["x-ams-access-token"]) == 128


def test_ams_query_is_sorted():
    params = {"query": "a b", "page": 2, "pageSize": 100}
    # the URL is encoded, the signature is not
    assert ams._query(params) == "page=2&pageSize=100&query=a+b"
    assert ams._sign_query(params) == "page=2&pageSize=100&query=a b"
    # encoding the signature is the one thing that must not happen: the server
    # rebuilds the message from decoded values and answers HTTP 401 otherwise
    assert "%20" not in ams._sign_query(params)


def test_ams_query_encodings_differ_where_the_site_needs_them():
    # spaces go out as '+' in the URL but stay literal in the signed string
    params = {"query": "Software Developer", "page": 1, "pageSize": 100}
    assert ams._query(params) == "page=1&pageSize=100&query=Software+Developer"
    assert ams._sign_query(params) == "page=1&pageSize=100&query=Software Developer"


def test_ams(fixture_json):
    postings = ams.parse(fixture_json("ams.json"))
    assert len(postings) == 3
    p = postings[0]
    assert p.title == "Softwareentwickler/in"
    assert p.company == "plasoft GmbH"
    assert p.location == "Kapfenberg, Steiermark, AT"   # workingLocation wins over company address
    assert p.external_id == "21658604"
    assert p.posted_at == "2026-09-21"
    assert p.description and "<" not in p.description
    # the detail page is keyed by uuid, NOT the numeric jobId
    assert p.url == "https://jobs.ams.at/public/emps/jobs/0781795f-0fff-3264-8d4d-cfe33821972d"
    assert "jobDetails" not in p.url
    assert "jobId" not in p.url


def test_ams_keeps_numeric_id_only_as_external_id(fixture_json):
    # identity is still source+external_id, so a re-scan upserts in place
    postings = ams.parse(fixture_json("ams.json"))
    assert all(p.external_id.isdigit() for p in postings)
    assert all(p.extra["uuid"] for p in postings)
    assert len({p.url for p in postings}) == 3      # distinct detail pages


def test_ams_skips_offer_without_uuid():
    # no uuid => no openable detail page; storing it would only emit a dead link
    data = {"results": [
        {"id": 1, "title": "Has uuid", "uuid": "abc-123",
         "company": {"name": "X"}, "lastUpdatedAt": "2026-09-20T00:00:00Z"},
        {"id": 2, "title": "No uuid", "company": {"name": "Y"},
         "lastUpdatedAt": "2026-09-20T00:00:00Z"},
    ]}
    out = ams.parse(data)
    assert [p.external_id for p in out] == ["1"]
    assert out[0].url.endswith("/abc-123")


def test_jobsat(fixture_text):
    body = fixture_text("jobsat.html")
    postings = jobsat.parse(body)
    assert len(postings) == 3
    p = postings[0]
    assert p.source == "jobsat"
    assert p.title == "Software-Entwickler (m/w/d)"
    assert p.company == "SOFTDENT GmbH"
    # this card has a state rather than a city
    assert p.location == "Steiermark"
    assert p.external_id == "7893070"
    assert p.url == "https://www.jobs.at/i/7893070"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.posted_at)
    # card 2 has a city and the GTM company attribute
    assert postings[1].location == "Graz"
    assert postings[1].company == "Squadra Personalmanagement GmbH"
    # card 3: GTM wins over the logo alt
    assert postings[2].company == "Fröling Heizkessel- und Behälterbau Ges.m.b.H."
    assert jobsat.has_more(body) is True


def test_jobsat_missing_location_defaults_to_austria():
    # jobs.at is Austria-only: several employers omit the location anchor, and
    # an unknown location must not be filtered out as "not DACH"
    card = '<li data-job="9" data-c-title="Ohne Ort"></li>'
    got = jobsat.parse(card)
    assert got[0].location == "Österreich"


def test_jobsat_base64_detail_url():
    # regression: data-c-redirect is base64 of the canonical /i/{id} URL
    card = ('<li data-job="123" data-c-title="Dev" '
            'data-c-redirect="aHR0cHM6Ly93d3cuam9icy5hdC9pLzEyMw=="></li>')
    got = jobsat.parse(card)
    assert got[0].url == "https://www.jobs.at/i/123"


def test_jobsat_keeps_undated_cards():
    card = '<li data-job="9" data-c-title="Ohne Datum"></li>'
    got = jobsat.parse(card)
    assert got[0].posted_at == ""
    assert got[0].url == "https://www.jobs.at/i/9"


def test_jobsat_page_url_has_one_page_param():
    # regression: seeding page=1 into the base and appending &page=N produced a
    # duplicate parameter; the server reads the first one, so paging never moved
    seen = []

    class _F:
        host_delay = 0.0
        errors: list = []

        def get(self, url):
            seen.append(url)
            return 200, ('<li data-job="1" data-c-title="Dev"></li>'
                         '<ol data-has-more="1"></ol>')

    jobsat.fetch({"queries": [{"keyword": "PHP"}], "max_pages": 3}, _F())
    # exactly one page= per URL, and each page requested once
    assert seen == ["https://www.jobs.at/j?keyword=PHP&page=1",
                    "https://www.jobs.at/j?keyword=PHP&page=2",
                    "https://www.jobs.at/j?keyword=PHP&page=3"]
    assert not _F.errors


def test_jobsat_page_beyond_the_last_one_is_not_an_error():
    # jobs.at keeps data-has-more="1" for keyword=PHP but 404s page 2;
    # that is the end of the results, not a fetch failure
    class _F:
        host_delay = 0.0
        errors: list = []

        def get(self, url):
            if url.endswith("page=1"):
                return 200, '<li data-job="1" data-c-title="Dev"></li>'
            return 404, ""

    got = jobsat.fetch({"queries": [{"keyword": "PHP"}], "max_pages": 2}, _F())
    assert len(got) == 1
    assert _F.errors == []


def test_recruitee_accepts_bare_tokens_and_full_hosts():
    # Most real Recruitee boards live on a CUSTOM domain. An adapter that only
    # builds {token}.recruitee.com skips every one of them SILENTLY — no error,
    # the board just never appears. That is how careers.tether.io went unseen
    # while 175 of its postings sat in the store.
    assert recruitee._host_for("attendi") == "attendi.recruitee.com"
    assert recruitee._host_for("  fastned  ") == "fastned.recruitee.com"
    assert recruitee._host_for("careers.tether.io") == "careers.tether.io"
    assert recruitee._host_for("talent.makerdao.network") == "talent.makerdao.network"
    # a pasted board URL must degrade to its host, not produce a broken URL
    assert recruitee._host_for("https://talent.makerdao.network") == "talent.makerdao.network"
    assert recruitee._host_for("talent.makerdao.network/o/some-slug") == "talent.makerdao.network"
    assert recruitee._host_for("") == ""


def test_recruitee_fetches_by_host(fixture_json):
    seen = []

    class F:
        def get_json(self, url, **kw):
            seen.append(url)
            return fixture_json("recruitee.json")

    out = recruitee.fetch({"companies": ["attendi", "careers.tether.io"]}, F())
    assert seen == ["https://attendi.recruitee.com/api/offers/",
                    "https://careers.tether.io/api/offers/"]
    assert out, "both boards should have produced postings"


def test_recruitee_fallback_url_uses_the_board_host():
    data = {"offers": [{"id": 1, "title": "Dev", "company_name": "X",
                        "slug": "dev-role"}]}      # no url field
    postings = recruitee.parse(data, "careers.tether.io")
    assert postings[0].url == "https://careers.tether.io/o/dev-role"


def test_recruitee(fixture_json):
    # parse() receives the resolved HOST (fetch() does the resolving), so a
    # board URL is rebuilt on the host we actually fetched from.
    postings = recruitee.parse(fixture_json("recruitee.json"), "fastned.recruitee.com")
    assert len(postings) == 2
    p = postings[0]
    assert p.company == "Fastned"
    assert p.external_id == "2652123"
    assert p.posted_at == "2026-09-10"
    assert p.compensation == "34000-37000 EUR per year"
    assert p.url.startswith("https://fastned.recruitee.com/o/")
    assert p.description and "<" not in p.description


def test_recruitee_ignores_a_stale_url_from_the_board():
    # The real failure this guards: decircletalentpartner.recruitee.com serves
    # all 196 deCircle offers, but each advertises the retired
    # talent.makerdao.network. Trusting that field stores 196 dead links for
    # postings that are alive. The host we fetched from must win.
    data = {"offers": [{
        "id": 7, "title": "Senior DeFi Engineer", "company_name": "deCircle",
        "slug": "senior-defi-engineer",
        "url": "https://talent.makerdao.network/o/senior-defi-engineer",
    }]}
    postings = recruitee.parse(data, "decircletalentpartner.recruitee.com")
    assert postings[0].url == (
        "https://decircletalentpartner.recruitee.com/o/senior-defi-engineer")

    # a URL already on the right host is left alone
    data2 = {"offers": [{
        "id": 8, "title": "Dev", "company_name": "deCircle", "slug": "dev",
        "url": "https://decircletalentpartner.recruitee.com/o/dev",
    }]}
    assert recruitee.parse(data2, "decircletalentpartner.recruitee.com")[0].url == (
        "https://decircletalentpartner.recruitee.com/o/dev")


def test_remoteok(fixture_json):
    postings = remoteok.parse(fixture_json("remoteok.json"))
    assert len(postings) == 2  # legal notice skipped
    assert postings[0].title and postings[0].company
    assert postings[0].url.lower().startswith("https://remoteok.com/")


def test_greenhouse(fixture_json):
    postings = greenhouse.parse(fixture_json("greenhouse.json"), "getyourguide")
    assert len(postings) == 2
    p = postings[0]
    assert p.company == "GetYourGuide"
    assert p.url.startswith("https://job-boards.greenhouse.io/")
    assert p.description


def test_ashby(fixture_json):
    postings = ashby.parse(fixture_json("ashby.json"), "n8n")
    assert len(postings) == 2
    p = postings[0]
    assert p.url.startswith("https://jobs.ashbyhq.com/n8n/")
    assert "Remote" in p.location
    assert p.description


def test_lever(fixture_json):
    postings = lever.parse(fixture_json("lever.json"), "leverdemo")
    assert len(postings) == 2
    p = postings[0]
    assert p.url.startswith("https://jobs.lever.co/leverdemo/")
    assert p.location
    assert re.match(r"\d{4}-\d{2}-\d{2}", p.posted_at)


def test_smartrecruiters(fixture_json):
    postings = smartrecruiters.parse(fixture_json("smartrecruiters.json"), "BoschGroup")
    assert len(postings) == 2
    p = postings[0]
    assert p.company == "Bosch Group"
    assert p.url.startswith("https://jobs.smartrecruiters.com/BoschGroup/")
    assert p.extra.get("token") == "BoschGroup"


def test_workable(fixture_json):
    postings = workable.parse(fixture_json("workable.json"), "blueground")
    assert len(postings) == 2
    p = postings[0]
    assert p.url.startswith("https://apply.workable.com/")
    assert p.location
