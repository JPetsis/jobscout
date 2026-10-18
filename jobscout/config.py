"""Workspace layout, config loading (YAML), and the default config template."""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG = '''\
# jobscout config - everything here is editable; rerun `jobscout scan` after changes.
# Data lives in ~/.jobscout (a hidden folder). Shortcuts:
#   jobscout config --open   reveal it in Finder
#   jobscout config --edit   open this file in your editor
# No API keys, no accounts: every source below is a public endpoint.

preferences:
  max_age_days: 21        # drop postings older than this when a date is known
  # How many matches the app loads. They load best-scored first, and the window
  # filters what it has, so this only needs to be large enough to cover the
  # board: the footer says when it is not.
  max_results: 2000
  # Look for new openings when the window opens, if the last scan is older than
  # this many hours. 0 turns it off and leaves scanning to the button. Nothing
  # time-based works without this: "fresh from the source" needs regular looks,
  # and so does anything measured from posting history.
  scan_if_older_hours: 24
  # How recent a posting from the employer's own board has to be to count as
  # "fresh from the source" in the app. Only that tier gets the mark: an
  # aggregator carrying the same role later is a republisher, and the window
  # is the point at which you are early.
  insider_days: 7
  # rough score anchors: 6 = right title + one more signal; 10 = right title
  # + a technology you actually want. Calibrate anytime with `jobscout stats`.
  min_score: 6            # below this a posting is stored but not shown in digests
  # 10, not 12: measured on a real store, the histogram breaks hard at 10
  # (192 rows at >=9, then 21 at >=10, 4 at >=11). 12 leaves almost nothing.
  strong_threshold: 10

  # --- TITLE rules (the job title only) ---
  # scoring terms are compound-friendly: "entwickler" also hits "Softwareentwickler"
  title_terms:
    - software
    - entwickler
    - developer
    - engineer
    - programmierer
    - backend
    - back-end
    - fullstack
    - full-stack
    - web
    - platform
    - devops
    - sre
  title_boost: 3          # points per title term hit
  title_cap: 6            # total cap for title points
  # whole-word match in TITLE -> filtered out
  # (role-type words belong here, not in exclude_terms)
  title_deny:
    - sales
    - vertrieb
    - account executive
    - recruiter
    - recruiting
    - praktikum
    - praktikant
    - internship
    - intern
    - werkstudent
    - working student
    - ausbildung
    - lehrstelle
    - duales studium

  # --- STACK rules (title + description) ---
  stack_terms:
    - python
    - php
    - laravel
    - symfony
    - typo3
    - wordpress
    - javascript
    - typescript
    - react
    - vue
    - node.js
    - sql
    - postgres
    - mysql
    - redis
    - docker
    - kubernetes
    - rest
    - api
    - graphql
    - aws
    - azure
    - gcp
    - terraform
    - ci/cd
    - linux
    - git
  stack_boost: 2          # points per stack term hit
  stack_cap: 8            # total cap for stack points
  stack_min_hits: 0       # 1+ requires at least N stack hits, everything else is filtered
  # stack points only count when the TITLE is role-relevant (title_terms above).
  # Without this, company boilerplate that lists every technology inflates
  # non-dev roles; set to false if you want stack points everywhere.
  stack_needs_title_match: true

  # --- DESCRIPTION rules (the job description only) ---
  description_terms: []   # e.g. [tdd, "open source", "pair programming"]
  description_weight: 1
  description_cap: 6
  description_deny: []    # e.g. [reisebereitschaft, schichtarbeit] -> filtered

  # --- PERMANENCE (a positive signal only: no word, no points) ---
  # Open-ended contract. Scanned: title + description, and the description
  # may carry a contract note a source parsed from its listing (academics.de).
  permanent_terms: [unbefristet, permanent, festanstellung]
  permanent_boost: 4
  permanent_cap: 4
  # The inverse marker (0 points): no permanence word, but the text says
  # fixed-term -> a "befristet" pill in listings. "befristet" never fires
  # on "unbefristet" (the elif against a real perm hit guards it).
  temporary_terms: [befristet, fixed-term, fixed term]
  # Academic-rank titles are filtered before scoring. Boundary-FRIENDLY
  # matching on purpose, so compounds hit ("Universitätsprofessur" ->
  # "professur"). Empty list switches it off.
  academic_deny: [professur, professorship, professor, postdoc, postdoktorand,
                  doktorand, doktorat, phd, w1, w2, w3, tenure, universitätsassistent]
  # Complete the detail page of a new MATCH once when its permanence is not
  # yet visible, then rescore -- so the permanence boost applies to every
  # match, not only to near-misses (a permanent 13 must outrank a fixed-term
  # 12). Rows are probed at most once ever; this is the per-scan budget.
  # 0 = off.
  detail_probe_per_scan: 10

  # --- ANYWHERE keywords (title + company + location + description) ---
  keywords: [agile, scrum, kanban]
  keyword_weight: 1
  keyword_cap: 8
  # whole-word, anywhere -> filtered out of digests (stored, with reason)
  exclude_terms:
    - unpaid
    - unbezahlt
    - commission only
    - personalvermittlung
    - arbeitsvermittlung

  # --- LOCATION rules ---
  # a known location must match one of these to score; deny wins over allow.
  # empty locations are always kept unless require_location_match is true.
  locations_allow:
    - remote
    - homeoffice
    - home office
    - hybrid
    - anywhere
    - worldwide
    - weltweit
    - berlin
    - deutschland
    - germany
    - österreich
    - austria
    - wien
    - vienna
    - zürich
    - zurich
    - schweiz
    - switzerland
    - europe
    - europa
    - dach
    - münchen
    - munich
    - hamburg
    - köln
    - cologne
    - frankfurt
    - stuttgart
    - düsseldorf
    - leipzig
    - dresden
    - nürnberg
    - graz
    - linz
    - salzburg
    - innsbruck
    - basel
    - bern
    - lausanne
    - genf
    - geneva
    - zug
    - luzern
  locations_deny: []
  location_boost: 2
  remote_bonus: 1         # extra point when the location says remote / homeoffice
  require_location_match: false

  # --- SALARY (0 = off) ---
  # Known annual pay below this is filtered out. Conversions: year = 1x,
  # month = 12x, day = 260x, hour = 2080x. Postings without a parsed salary
  # are kept. No FX conversion: currencies are compared as numbers and shown
  # in the digest, so a USD figure is not silently treated as EUR.
  min_salary: 0

  # --- liveness of saved/applied postings ---
  verify_on_scan: true      # re-check saved/applied URLs during scans
  verify_max_age_days: 3    # only entries whose last check is older than this
  verify_per_scan: 15       # cap of checks per scan

sources:
  # sources run in this order; remove entries to disable them
  enabled: [linkedin, stepstone, xing, stellenanzeigen, karriere_at, jobs_ch,
            arbeitnow, berlinstartupjobs, ams, eures, jobsat, hn, remotive, wwr, remoteok,
            larajobs, workingnomads, nodesk, himalayas, greenhouse, ashby, recruitee,
            themuse, germantechjobs, swissdevjobs, jobup, jobscout24, jobware, talent,
            englishjobs, governmentjobs, jobicy, wordpressjobs, pythonjobs, fossjobs,
            academics, wissenschaftskarriere, euraxess, myscience]

  linkedin:
    # LinkedIn's public guest search (no login). Keep the budget small or
    # LinkedIn starts answering 429s; max_requests is a hard per-run cap.
    max_requests: 24
    fresh_days: 14        # only postings from the last N days (0 = no filter)
    queries:
      - { keywords: softwareentwickler, location: "Berlin, Germany", pages: 2 }
      - { keywords: php developer, location: Deutschland, pages: 1 }

  stepstone:
    # 25 results per page
    queries:
      - { keywords: softwareentwickler, location: Berlin, pages: 1 }

  xing:
    queries:
      - { keywords: softwareentwickler, location: berlin, pages: 1 }

  stellenanzeigen:
    queries:
      - { keywords: softwareentwickler, location: Berlin, pages: 1 }

  karriere_at:
    queries:
      - { keywords: softwareentwickler, pages: 1 }

  jobs_ch:
    # rows max is 20 (API limit) - use pages for more
    queries:
      - { keywords: softwareentwickler, location: "", pages: 3, rows: 20 }

  arbeitnow:
    pages: 2

  ams:
    # jobs.ams.at (Austrian public employment service). The JSON API is public;
    # each request is signed with a client-side HMAC-SHA512 over a key that
    # ships in the app bundle (see sources/ams.py). No account, no API key.
    page_size: 100         # the API rejects 200 with HTTP 400
    max_pages: 2           # pages per query
    max_age_days: 45
    sort_field: _LAST_MODIFIED
    sort_order: desc
    queries:
      - { keywords: Softwareentwickler }
      - { keywords: Softwareentwickler_in }
      - { keywords: Software Developer }
      - { keywords: DevOps Engineer }

  eures:
    # EU official portal, public JSON search at europa.eu/eures/api.
    # locationCodes are NUTS codes: at = Austria, de = Germany, ch = Switzerland.
    search_code: TITLE    # EVERYWHERE is very fuzzy (KFZ techs match); TITLE is precise
    publication_period: LAST_MONTH
    per_page: 50          # the API rejects >50 with HTTP 400
    max_pages: 2          # pages walked per query
    max_age_days: 45
    queries:
      - { keywords: software engineer, location: at }
      - { keywords: software developer, location: at }
      - { keywords: developer, location: at }
      - { keywords: devops, location: at }

  hn:
    max_comments: 120     # top-level comments read from the latest "Who is hiring?"

  remotive:
    limit: 100
    search: ""

  wwr:
    categories: [remote-programming-jobs]

  himalayas:
    # 20 jobs per page, hard rate limit (HTTP 429): keep the budget small.
    max_pages: 20        # newest N pages walked per scan
    max_age_days: 21     # stop paging once postings older than this appear

  themuse:
    # The Muse's public API needs no key. Registering only raises the rate
    # limit, so `api_key` is optional.
    #
    # It has NO free-text search: the axes are category, location and level.
    # Leave `categories` empty to fetch everything newest-first and let the
    # scorer decide, or name categories to fetch narrower and faster.
    # https://www.themuse.com/developers/api/v2
    pages: 5                 # 20 postings per page
    categories: []           # e.g. [Software Engineering, Data Science]
    location: ""             # e.g. United States, New York, NY ("" = anywhere)
    api_key: ""              # optional

  workday:
    # The API behind a large share of large-employer careers sites. No key.
    # Add employers under `companies.workday.tokens` below; with none added
    # this source does nothing, which is why it is off by default.
    pages: 3                 # 20 postings per page, per query
    queries: []              # keywords only; from your profile unless set

  adzuna:
    # Needs a free key — leave this source disabled until you have one.
    # https://developer.adzuna.com/signup (instant, no card).
    # Unlike the others, `country` is what makes it useful outside Germany:
    # it is a path segment and picks both the market and the salary currency
    # (us, gb, de, at, ch, nl, fr, au, ca).
    # Free tier: 25 requests/min, 250/day — ONE request per scan is the
    # default for that reason.
    app_id: ""
    app_key: ""
    country: us              # us, gb, de, at, ch, nl, fr, au, ca
    what: ""                 # search keywords ("" = everything)
    where: ""                # location restriction
    pages: 1                 # requests per scan

  usajobs:
    # Needs a free key — leave this source disabled until you have one.
    # https://developer.usajobs.gov/apirequest/ (a form; the key is emailed).
    # US federal agencies only. The email must be a real address: it is sent
    # as the User-Agent, which is how USAJOBS identifies you.
    key: ""
    email: ""
    keyword: ""              # "" = everything
    results_per_page: 25     # max 500

  jobup:
    # jobup.ch (French-speaking Switzerland): the same public API family as jobs.ch.
    queries: []              # from your profile unless set

  jobscout24:
    # jobscout24.ch (Switzerland). Search param: SearchWhat; pager: `p`
    # (`page=` is silently ignored - use `p` in hand-written queries too).
    queries: []              # from your profile unless set

  jobware:
    # jobware.de (Germany). Result cards are buttons, not links - see
    # sources/jobware.py before touching the parser.
    queries: []              # from your profile unless set

  talent:
    # talent.com (aggregator, German front); k/l/p query parameters.
    queries: []              # from your profile unless set

  englishjobs:
    # English-speaking jobs in Germany. Category pages, not keyword search.
    categories: [remote]     # e.g. remote, visa-sponsorship, part-time
    pages: 2

  governmentjobs:
    # US public-sector board (NEOGOV), keyword search.
    queries: []              # from your profile unless set

  jobicy:
    # Free remote-jobs API, no key. Its notice asks for a visible credit
    # wherever its jobs are shown (the README credits it).
    count: 50                # max 50 per request
    max_pages: 3             # cursor pages walked per scan
    geo: ""                  # e.g. germany (server-side filter)
    industry: ""
    tag: ""

  teamtailor:
    # Every Teamtailor board exposes a JSON Feed. Add employers under
    # companies.teamtailor.tokens; with none added this source does nothing,
    # which is why it is off by default.
    max_pages: 2             # 100 postings per page

  academics:
    # academics.de (ZEIT group): universities, research institutes and the
    # public sector, every discipline. The listing URL ignores keyword
    # search, so the newest pages are walked: `pages` x 50 postings.
    pages: 2

  wissenschaftskarriere:
    # Wissenschaftskarriere (DUZ) academic board. Plain HTTP reaches the
    # newest 20 offers; the rest of its result page loads by script.

  euraxess:
    # EURAXESS, the EU's official researcher-jobs portal (all disciplines
    # incl. fellowships). 10 per page, newest first; the site's own paging
    # is zero-based, `pages` here is 1-based like everywhere else.
    pages: 2

  myscience:
    # myScience: science jobs in Germany, Switzerland and Austria. Keyword
    # search via ?q=; one page (21 postings) per query. Does not take a
    # location parameter.
    queries: []              # from your profile unless set

companies:
  greenhouse:
    # board tokens from https://boards.greenhouse.io/<token>
    tokens: [getyourguide, celonis, trivago, sumup]
  ashby:
    # board tokens from https://jobs.ashbyhq.com/<token>
    tokens: [n8n, langfuse, supabase]
  lever:
    # board tokens from https://jobs.lever.co/<token>
    tokens: []
  smartrecruiters:
    # company ids from https://jobs.smartrecruiters.com/<id> (e.g. BoschGroup)
    tokens: []
  workable:
    # account names from https://apply.workable.com/<account>/
    tokens: []
  teamtailor:
    # board tokens from https://<token>.teamtailor.com/jobs (e.g. teamviewer)
    tokens: []
  join:
    # company slugs from https://join.com/companies/<slug>
    tokens: []
'''

UI_SECTION_BODY = [
    'theme: system           # system | light | dark. Also switchable in the',
    '                        # window header; "system" follows your OS setting.',
]

UPDATES_SECTION_BODY = [
    '# Ask GitHub whether a newer release exists, at most once a day, and show a',
    '# notice in the window if there is one. Set check to false to stop the app',
    '# asking on its own; running `jobscout update` yourself still works.',
    'check: true',
    '# Install a newer version without asking. The download is verified before',
    '# anything is replaced, and the previous build is kept until the swap has',
    '# succeeded, so a failed update leaves the working version in place. Set to',
    '# false to be asked first.',
    'auto: true',
]

# The template and the create-on-demand path (cfgwrite.ensure_section) both come
# from these bodies, so a fresh install and an existing config upgraded in place
# cannot drift apart. SECTION_BODIES is what lets a settings write add a section
# a config written earlier never had.
DEFAULT_CONFIG += "\nui:\n" + "".join(f"  {line}\n" for line in UI_SECTION_BODY)
DEFAULT_CONFIG += "\nupdates:\n" + "".join(f"  {line}\n" for line in UPDATES_SECTION_BODY)

SECTION_BODIES: dict[str, list[str]] = {
    "ui": UI_SECTION_BODY,
    "updates": UPDATES_SECTION_BODY,
}

SOURCE_DEFAULTS: dict[str, dict] = {
    "linkedin": {"max_requests": 24, "fresh_days": 14, "queries": []},
    "stepstone": {"queries": []},
    "xing": {"queries": []},
    "stellenanzeigen": {"queries": []},
    "karriere_at": {"queries": []},
    "jobs_ch": {"queries": []},
    "arbeitnow": {"pages": 2},
    "berlinstartupjobs": {},
    "jobsat": {"queries": [{"keyword": "Softwareentwickler"},
                         {"keyword": "Software Entwickler"},
                         {"keyword": "Developer"},
                         {"keyword": "PHP"}],
               "max_pages": 2, "max_age_days": 45},
    "ams": {"page_size": 100, "max_pages": 2, "max_age_days": 45,
            "sort_field": "_LAST_MODIFIED", "sort_order": "desc"},
    "hn": {"max_comments": 120},
    "remotive": {"limit": 100, "search": ""},
    "wwr": {"categories": ["remote-programming-jobs"]},
    "remoteok": {},
    "larajobs": {},
    "workingnomads": {},
    "nodesk": {},
    "himalayas": {"max_pages": 20, "max_age_days": 21},
    "recruitee": {},
    "themuse": {"pages": 5, "categories": [], "location": "", "api_key": ""},
    "workday": {"pages": 3},
    "adzuna": {"country": "us", "pages": 1},
    "usajobs": {"results_per_page": 25},
    "eures": {"search_code": "TITLE", "publication_period": "LAST_MONTH",
              "per_page": 50, "max_pages": 2, "max_age_days": 45},
    "greenhouse": {},
    "ashby": {},
    "lever": {},
    "smartrecruiters": {},
    "workable": {},
    "germantechjobs": {},
    "swissdevjobs": {},
    "jobup": {"queries": []},
    "jobscout24": {"queries": []},
    "jobware": {"queries": []},
    "talent": {"queries": []},
    "englishjobs": {"categories": ["remote"], "pages": 2},
    "governmentjobs": {"queries": []},
    "jobicy": {"count": 50, "max_pages": 3},
    "teamtailor": {"max_pages": 2},
    "join": {},
    "wordpressjobs": {},
    "pythonjobs": {},
    "fossjobs": {},
    "academics": {"pages": 2},
    "wissenschaftskarriere": {},
    "euraxess": {"pages": 2},
    "myscience": {"queries": []},
}


def home() -> Path:
    """The workspace in use: the active profile, or the base ~/.jobscout.

    Everything that reads or writes the workspace goes through here, which is
    why switching profiles needs no other code to know profiles exist. The
    resolution itself lives in jobscout/profiles.py.
    """
    from . import profiles
    return profiles.resolve_root()


@dataclass
class Config:
    root: Path
    path: Path
    prefs: dict = field(default_factory=dict)
    enabled: list = field(default_factory=list)
    sources_cfg: dict = field(default_factory=dict)
    companies: dict = field(default_factory=dict)
    goals: dict = field(default_factory=dict)
    active_goal: str = ""
    raw: dict = field(default_factory=dict)


def load_config(root: Path | None = None) -> Config:
    root = Path(root) if root else home()
    ypath = root / "config.yaml"
    tpath = root / "config.toml"
    if ypath.exists():
        path = ypath
        raw = yaml.safe_load(ypath.read_text(encoding="utf-8")) or {}
    elif tpath.exists():
        path = tpath
        raw = tomllib.loads(tpath.read_text(encoding="utf-8"))
        print("note: using legacy config.toml - rename it to config.yaml when convenient",
              file=sys.stderr)
    else:
        raise SystemExit(f"no config found at {ypath}\nrun: jobscout init")
    if not isinstance(raw, dict):
        raise SystemExit(f"config at {path} did not parse to a mapping")
    sources_raw = raw.get("sources") or {}
    sources_cfg = {k: dict(v) for k, v in sources_raw.items()
                   if k != "enabled" and isinstance(v, dict)}
    enabled = list(sources_raw.get("enabled") or [])
    companies = {k: dict(v) for k, v in (raw.get("companies") or {}).items() if isinstance(v, dict)}
    # goals are optional: no `goals:` block means jobscout behaves exactly as
    # before, with no goal filtering anywhere.
    goals: dict = {}
    active = ""
    gblock = raw.get("goals")
    if isinstance(gblock, dict):
        gblock = dict(gblock)
        active = str(gblock.pop("active", "") or "")
    elif isinstance(gblock, str):
        active = gblock
        gblock = {}
    try:
        from .goals import load_goals
        goals = load_goals({"goals": gblock})
    except ImportError:
        goals = {}
    return Config(
        root=root,
        path=path,
        prefs=dict(raw.get("preferences") or {}),
        enabled=enabled,
        sources_cfg=sources_cfg,
        companies=companies,
        goals=goals,
        active_goal=active,
        raw=raw,
    )


def source_settings(cfg: Config, name: str) -> dict:
    """Merged settings: defaults <- sources.<name> <- company tokens <- generated queries."""
    s = dict(SOURCE_DEFAULTS.get(name, {}))
    s.update(cfg.sources_cfg.get(name, {}))
    s["enabled"] = name in cfg.enabled
    s["companies"] = list(cfg.companies.get(name, {}).get("tokens", []) or [])
    # Queries come from the profile at runtime (jobscout/querygen.py), merged
    # with any hand-written ones so explicit tuning always wins.
    try:
        from .querygen import merge_queries
        merged = merge_queries(cfg, name, cfg.prefs)
    except Exception:                                        # noqa: BLE001
        merged = list(s.get("queries") or [])
    if merged:
        s["queries"] = merged
    return s


def init_workspace(root: Path | None = None) -> list[str]:
    """Create the workspace if needed. Returns the list of files created."""
    root = Path(root) if root else home()
    created: list[str] = []
    root.mkdir(parents=True, exist_ok=True)
    cfg_yaml = root / "config.yaml"
    cfg_toml = root / "config.toml"
    if not cfg_yaml.exists() and not cfg_toml.exists():
        cfg_yaml.write_text(DEFAULT_CONFIG, encoding="utf-8")
        created.append(str(cfg_yaml))
    (root / "reports").mkdir(exist_ok=True)
    return created
