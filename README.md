# jobscout

Local-first job search for DACH + remote roles. One command fetches postings from
public job boards (LinkedIn's guest API, StepStone, XING, stellenanzeigen.de,
karriere.at, jobs.ch, Arbeitnow, Berlin Startup Jobs, HN Who is hiring and more),
scores them against your preferences with transparent rules, dedupes across
sources, and writes a markdown digest.

**No API keys, no accounts, no metered calls.** Every source is a public endpoint
that a browser also uses. Your data (preferences, postings, digests) lives in
`~/.jobscout/` and never leaves your machine.

This is the "no tokens" sibling of [agent-data/job-search](https://github.com/agent-data/job-search):
same idea (preferences → search → judgment → digest), but the search layer is
local scraping instead of a metered marketplace, and the judgment is a
deterministic rule engine instead of LLM calls. An agent (Claude Code, Hermes, …)
can still drive it — the CLI is the interface.

## Quickstart

```bash
cd ~/Projekte/Personal/jobscout
uv sync                  # creates .venv with the jobscout CLI + pytest
uv run jobscout init     # writes ~/.jobscout/config.yaml with sensible defaults
uv run jobscout config --edit     # opens it (--open reveals it in Finder)
uv run jobscout scan     # fetch + score + store new postings
uv run jobscout digest   # markdown digest in ~/.jobscout/reports/
```

Or without uv: `python3.13 -m jobscout ...` works from the repo root (only PyYAML
needed), and `pip install -e .` gives you the `jobscout` command.

## Commands

Global flags: `--version`, `--no-color` (or `NO_COLOR=1`). Terminal output is
color-coded (strong = gold, matches = cyan, inactive = dim) and switches itself
off when piped.

| command | what it does |
|---|---|
| `jobscout init` | create `~/.jobscout/` with a commented default config |
| `jobscout config` | print the config path (`--open` reveals it in Finder, `--edit` opens your editor) |
| `jobscout scan` | fetch all enabled sources, score, dedupe, store |
| `jobscout digest` | run digest: new postings only; `--all` writes `reports/<date>-matches.md` with everything matching your current profile |
| `jobscout list` | stored matches |
| `jobscout goals` | list every defined goal with its live match count |
| `jobscout goal use NAME` | activate one goal (the default view); `--off` or bare `goal off` deactivates |
| `jobscout show ID` | one posting; fetches the full description on first view |
| `jobscout triage` | walk unmarked matches one by one: `o`pen, `s`aved, `a`pplied, `x` dismiss, `n`ext |
| `jobscout verify` | re-check saved/applied listings for dead pages; runs automatically during scans |
| `jobscout add-company URL` | find the Greenhouse/Ashby/Lever/Workable/SmartRecruiters board behind a careers page, validate it live, add the token to your config and enable the source |
| `jobscout mark ID STATUS` | track what you did with a posting |
| `jobscout rescore` | re-apply your current preferences to everything stored |
| `jobscout stats` | per-source counts and recent runs |

### Every flag

```
jobscout scan     [--only stepstone,xing] [--quiet]
jobscout digest   [--run N] [--all] [--top N] [--open]

jobscout list     [--min-score N] [--status new|saved|applied|dismissed]
                  [--source NAME] [--filtered] [--limit N] [--json]
                  [--goal NAME] [--all-goals]

jobscout goals
jobscout goal     use <name> | <name> | --off

jobscout show     ID [--refresh] [--json]

jobscout triage   [--limit N] [--min-score N] [--source NAME]
                  [--goal NAME] [--all-goals]

jobscout verify   [--status saved,applied] [--all] [--limit N] [--quiet]

jobscout mark     ID STATUS
jobscout rescore
jobscout stats
jobscout add-company URL
```

### Goals

A goal is **one standalone target, never a bundle**. "Austria, senior,
Düsseldorf" is not a goal, it is three goals and two countries that can never be
true at once. Coherence is the acceptance test.

Many goals can be defined, exactly one is active. The active goal is the
**default view** for `list` and `triage`, so the count you see is a count you
can act on; `--all-goals` escapes to the full pool and `--goal NAME` peeks at
another without switching. Goals are **optional**: with no `goals:` block in
your config jobscout behaves exactly as it did before, and a goal only ever
narrows the base profile, it never resurrects a row the rule engine filtered.

```bash
uv run jobscout goals             # every goal + live match count
uv run jobscout goal use austria  # make it the default view
uv run jobscout goal off          # back to the full pool
```

A goal matches on the **title** (plus company) by default. That default matters:
a `seniority` goal that reads the description fires on prose like "lead a team"
and matches Tether and Field Service Engineer rows, which buries the real senior
roles in triage. Use `scope: all` for topic goals, never for rank. A `salary`
goal never matches unknown pay, because no number is not the same as enough.

## Location: countries, not cities

`country_allow: [de]` is the location gate. It answers "which country is
this?" instead of "does the location string contain one of the words I typed
down", because a country is not a term. Germany has ~11,000 municipalities and
no finite list of names is a country; a hand-written list of 60 cities
silently drops Oggelshausen, Raaba-Grambach and every `bundesweit` posting
while accepting Copenhagen, because `hagen` is also a village in
Hagenberg im Mühlkreis.

Resolution runs in three tiers, entirely offline (`jobscout/geo.py`):

1. **Country, subdivision and ISO code names**, in the languages these boards
   emit. German `Bundesländer`, Austrian and Swiss cantons, US state names,
   and a curated `COUNTRY_WORDS` table. This tier is a closed set, so a match
   is strong evidence.
2. **`geonamescache`**, a gazetteer of ~234,000 cities down to 500 population
   bundled as JSON in the package. No network call at runtime, which keeps the
   local-first promise. Costs a one-off 2.6s index build, then ~0.03ms per
   location.
3. **Remote / nationwide / unknown**, which carry no country at all.

The rule the whole thing rests on: **only a location that positively resolves
to a country you did not allow is filtered.** An unresolvable location —
`Remote`, `Quellenhof-Weilerhof`, an empty field, `N/A` — is an *absence* of
evidence, so it is kept and ranked, never dropped. `remote_countries_ok: false`
is the strict variant for someone who wants a real country on every row.

Two traps, both of which produce silent nonsense rather than an error:

- **Substring matching is never safe for short codes.** `at` inside a word, or
  `se` inside `Münster`, invents countries. Codes only ever match as whole
  tokens. The same is true of US state *codes*: `DE` is Delaware and Germany,
  `CA` is California and Canada, so the colliding ones are deliberately missing
  from the table. State *names* are unambiguous and all of them are present.
- **Punctuation has to be split before lookup.** `norm()` keeps the comma, so
  `Toulouse, Occitanie` tokenizes to `['toulouse,', 'occitanie']` and the
  gazetteer never sees the city — which is to say it never sees
  *comma-separated locations*, the most common format there is.

`locations_prefer` is a **ranking boost**, never a gate: Hamburg and Tübingen
earn points, and you never have to type a village. The legacy
`locations_allow` / `locations_deny` / `require_location_match` still work
unchanged for anyone who wants the old word-list behaviour.

## How a scan works

1. **Fetch** — each enabled source is scraped politely (browser UA, per-host
   pacing, retries); one broken source never kills the run, it lands in the
   error footer of the digest instead.
2. **Dedupe** — a fingerprint per posting plus a cross-source key
   (`company+title+location`), so the same role found via StepStone and LinkedIn
   appears once.
3. **Score** — deterministic, per-field rules from `[preferences]` in your config.
   Each field has its own list and weight: **title** (`title_terms`, `title_deny`),
   **stack** (`stack_terms`, searched in title + description, with
   `stack_min_hits` as a "must have at least N" gate), **description**
   (`description_terms`, `description_deny`), anywhere (`keywords`,
   `exclude_terms`), and **location** (`country_allow`,
   `locations_prefer`, `remote_bonus`). Every point is stored as a reason
   and digests show the exact breakdown
   (`why: title:engineer +3 · stack:python +2 · country:de +2`).
   All scoring terms match whole words and compound parts (so `entwickler` hits
   `Softwareentwickler`, but `git` does not hit `digital`); filter lists
   (`title_deny`, `description_deny`, `exclude_terms`) match whole words only
   (so `sales` does not drop `Salesforce Developer`). Stack points only count
   when the title is role-relevant (`stack_needs_title_match`), which keeps
   boilerplate-heavy non-dev listings from inflating. Bands: `min_score`
   (default 6) and `strong_threshold` (default 12); `jobscout stats` prints a
   score histogram whenever you want to recalibrate.
   `jobscout rescore` re-applies edited preferences to everything already stored
   without re-fetching, and `jobscout scan` does that automatically when it
   notices the preferences changed since the last run.
   Pay ranges are parsed out of compensation fields and description text
   (EUR/USD/GBP/PLN/CHF; hourly, daily, monthly, yearly; B2B and German
   `ab X € monatlich` styles) and shown as a chip (`€60k–80k/yr`, `$45–70/hr`);
   `min_salary` then filters out postings whose known annual pay is below your
   floor (year = 1x, month = 12x, day = 260x, hour = 2080x). Postings without a
   parsed salary are always kept, and no FX conversion happens — currencies are
   compared as numbers and shown as-is. The parser is deliberately conservative
   with bare numbers: ids, timestamps, headcounts, revenue figures and 401(k)
   plans are never read as pay. `jobscout show ID` prints the exact text a
   reading came from.
4. **Digest** — the run digest lists only postings seen for the first time in
   that run, so a second scan the same day is small by design (everything else
   is already stored). After editing preferences, `jobscout digest --all` lists
   every stored posting that matches your current profile
   (`reports/<date>-matches.md`). Both views show strong matches, matches,
   filtered-out items with reasons, and run stats.

## Working through the matches

Everything stored starts as `new`. `jobscout triage` walks unmarked matches one
at a time (score, title, company, salary chip, why, URL) and takes a single
keypress: `o` or Enter opens the posting in your browser, `s`/`a`/`x` mark it
saved/applied/dismissed, `n` skips, `q` stops. Piped input works too, so an
agent or a script can drive the same loop.

`jobscout verify` checks saved/applied URLs for dead listings. Known ATS job
URLs (Greenhouse, Ashby, Lever, SmartRecruiters) are probed through the public
board APIs — their web pages are JS shells that answer 200 for anything —
and everything else through the page itself (HTTP 404/410 plus a set of
"no longer accepting applications"-style phrases). Blocked responses
(401/403/429) are reported as unknown, never as dead. Scans re-check up to
`verify_per_scan` saved/applied entries whose last check is older than
`verify_max_age_days`, and the digest flags dead ones.

`jobscout add-company https://careers.example.com` stops the company list from
being hand-maintained: it reads the careers page (or a job URL), detects the
board behind it, validates the token against the ATS API, appends it to
`companies.<source>.tokens` in your config, and enables the source if it was
not in `sources.enabled` yet.

## Sources

Working today (all verified live, all free):

| source | region | how |
|---|---|---|
| `linkedin` | DACH | public guest search API, no login (rate-limited by design: `max_requests` per run) |
| `stepstone` | DE | result pages + JSON-LD detail pages |
| `xing` | DACH | server-rendered search + detail pages |
| `stellenanzeigen` | DE | server-rendered search pages |
| `karriere_at` | AT | result list from `window.VUE_INITIAL_STATE` |
| `jobs_ch` | CH | public JSON search API |
| `arbeitnow` | DE/remote | public API |
| `berlinstartupjobs` | Berlin | RSS feed |
| `hn` | global | Algolia + Firebase (latest "Who is hiring?" thread) |
| `remotive`, `wwr`, `remoteok`, `workingnomads`, `nodesk` | remote | free APIs / RSS |
| `himalayas` | global remote | public JSON API, cursor-paginated; newest pages only (hard rate limit), structured salary fields folded into the salary parser |
| `larajobs` | remote / PHP | RSS feed, Laravel/PHP only; the feed carries no body, so the job page is read on first `show` |
| `recruitee` | company boards | public `<company>.recruitee.com/api/offers/` JSON, one request per board, structured salary |
| `eures` | EU (NUTS countries, default AT) | official EURES portal JSON search, public, no key; `locationCodes` are NUTS country codes, the canonical vacancy page doubles as the liveness URL |
| `ams` | Austria | `jobs.ams.at` public JSON API; requests are signed with a client-side HMAC-SHA512 over a key shipped in the public app bundle (no account, no API key) |
| `jobsat` | Austria | `jobs.at` public search, server-rendered HTML (no JSON API, no key); 15 cards per page, detail URL is base64 in `data-c-redirect`, an empty location means "Österreich" |
| `greenhouse`, `ashby`, `lever`, `smartrecruiters`, `workable` | company boards | public ATS APIs — add your target companies under `companies:` in the config, or let `jobscout add-company <careers-url>` find them |

Known limitations, on purpose:

- **Indeed**: actively blocks scripted access ("Security Check"), no public API
  without tokens. Not supported; StepStone/XING/stellenanzeigen cover the same
  German market.
- **Bundesagentur für Arbeit**: its jobsuche API now rejects anonymous requests
  (403) and needs a free developer registration with OAuth client credentials.
  If you register, the adapter is a small addition — not included by default to
  keep the "no accounts" promise.
- **willhaben (AT)**: job board returns 404 for scripted access; karriere.at
  covers Austria.
- XING/StepStone don't show per-card dates for some layouts — those postings
  simply have no `posted` date (they're never dropped for it).

## Scheduling

Run it whenever you like, or wire it to cron/launchd:

```cron
0 8 * * * cd ~/Projekte/Personal/jobscout && uv run jobscout scan --quiet && uv run jobscout digest
```

(Hermes users: ask the agent to create a cronjob wrapping the same command.)

## Layout

```
jobscout/            # code (this repo)
├── jobscout/        # the package (Python + PyYAML)
│   ├── sources/     # one module per job board
│   ├── cli.py       # jobscout <command>
│   ├── score.py     # the rule engine
│   ├── salary.py    # pay parsing + formatting
│   ├── liveness.py  # dead-listing checks (ATS-aware)
│   ├── companies.py # ATS detection + config edits
│   ├── triage.py    # the one-by-one review loop
│   ├── digest.py    # markdown digest writer
│   └── db.py        # sqlite storage + dedup
└── tests/           # pytest suite + real captured fixtures

~/.jobscout/         # your data (hidden folder, never in the repo)
├── config.yaml      # preferences + sources + companies
├── jobscout.db      # postings, scores, runs
└── reports/         # digests
```

## Tests

```bash
uv run pytest
```

The suite covers the text helpers, the scoring engine (caps, hard filters,
stale/unknown dates, `min_salary`), the database (fingerprints, cross-source
dedup, statuses, schema migration), every parser against real captured fixtures,
salary parsing (ranges, currencies, periods, and the junk it must ignore: ids,
headcounts, revenue figures, 401(k) plans), liveness checks (ATS API probes and
page markers), the triage loop, company detection + config edits, and an
end-to-end scan→digest run.
