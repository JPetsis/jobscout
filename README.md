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

## Desktop app

The same core in a native window: pywebview renders the front-end in WKWebView on
macOS, WebView2 on Windows and Qt WebEngine on Linux, and that front-end is a
client of `jobscout/api.py` — the window and the CLI show the same numbers,
because there is only one rule engine.

```bash
uv sync --extra app    # pywebview; on Linux the extra also pulls its [qt] backend
uv run jobscout init   # the window needs ~/.jobscout/config.yaml to exist
uv run jobscout-app
```

The window shows your stored matches as a card grid with a search box, a score
band (all / strong / matches), and source and status filters. Everything a
non-technical user needs is in it:

- **Read a match.** Click a card for the full posting — salary, liveness, why it
  scored, and the whole description, fetched live when the source only put a
  teaser in the listing. `Save` / `Applied` / `Dismiss` write straight to the
  database; `S` / `A` / `X` do the same from the keyboard.
- **Scan.** The Scan button runs the identical scan loop the CLI runs, with a
  progress bar and a Cancel button. With a source selected in the filter it
  scans only that source. If a source needs attention — a missing API key, say —
  the scan says so by name and prints what to do about it, rather than
  reporting zero jobs and leaving you to guess.
- **Choose which boards to scan.** Settings has a checkbox per job board, with
  real names rather than config keys, grouped by the market each serves (DACH,
  US, Europe, global/remote, academic, and the per-company boards). Tick what
  you want. Your existing order is preserved, and selecting nothing warns you
  instead of letting a scan quietly do nothing.
- **Edit your profile.** The job titles you want, your skills and how much you
  care about each, the countries and places you'll accept, and how strict the
  cutoffs are.
- **Several people, or several searches.** If more than one profile exists the
  window asks who's using it before it opens, and each profile is a separate
  workspace with its own settings, job boards and saved postings. See
  [Profiles](#profiles).
- **Pick your colours.** A switcher in the header: follow the system, or pin
  light or dark. Stored in `config.yaml` as `ui.theme`.
- **Install updates.** jobscout checks for a newer release once a day and, by
  default, installs it by itself: the download is checksum-checked first, and
  the app restarts on the new version. Both halves are settings if you would
  rather be asked. See [Updating](#updating).
- **Recover a backup.** If the window opens and finds no workspace, it looks
  for a backup first and offers to restore it instead of just failing. See
  [Restoring a backup](#restoring-a-backup).

Settings writes to `config.yaml` through `jobscout/cfgwrite.py`, which edits the
file line by line. That matters: a `yaml.safe_load` / `safe_dump` round-trip
leaves every value correct and silently deletes all 137 comment lines that
document the file. A same-value save is a byte-identical no-op, and the values
the window accepts are validated before they reach disk.

Digests and the terminal triage loop stay in the CLI (`jobscout digest`,
`jobscout triage`).


## Profiles

A profile is a whole workspace, not a filter over a shared pool:

```
~/.jobscout/                    the base, and the "Default" profile
  config.yaml
  jobscout.db
  active-profile                which profile is in use
  profiles/
    Archaeology/                a profile: its own config, database and reports
    Freelance/
```

That distinction matters for two cases. A **second person** on the same computer
gets a genuinely empty app rather than the first person's postings filtered
differently. And one person can keep **two searches configured completely
differently** — different titles, different skills, different countries —
without either contaminating the other.

A new profile starts from the shipped template with no database at all, so it
never inherits the profile you were just using. Switch from the window
(Settings → the Profile block, or the picker that opens at launch whenever more
than one profile exists), or from a terminal:

```
jobscout profile                     # what exists, and what is in use
jobscout profile create Archaeology  # a new, empty workspace
jobscout profile use Archaeology     # switch to it
jobscout profile remove Archaeology --yes
```

`remove` takes a backup into `~/jobscout-backups/` first and deletes nothing if
that backup fails. The archive is named after the profile, because a directory
of backups gets browsed by filename, and `jobscout-2026-...tar.gz` does not say
which profile is inside it.

Your existing install simply *is* the Default profile — the base root has not
moved, so there is nothing to migrate.

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
| `jobscout discover` | for URLs a source blocks, find and test a public API to check liveness against (reads URLs on stdin) |
| `jobscout profile` | list profiles; `use NAME` switches, `create NAME` makes an empty one, `remove NAME --yes` deletes one (after taking a backup) |
| `jobscout backup` | archive the workspace (config, database, reports) to one file; `--list` shows them |
| `jobscout restore` | put a backup back; `--from FILE` picks one, `--overwrite` replaces an existing workspace |
| `jobscout update` | check for a newer build; `--check` only reports, `--yes` installs it, `--from FILE` installs a build you already downloaded |

### Every flag

```
jobscout scan     [--only stepstone,xing] [--quiet]
jobscout digest   [--run N] [--all] [--top N] [--open]

jobscout list     [--min-score N] [--status new|saved|applied|dismissed]
                  [--source NAME] [--filtered] [--limit N] [--json]
                  [--goal NAME ...] [--all-goals]

jobscout goals
jobscout goal     use <name> | <name> | --off

jobscout show     ID [--refresh] [--json]

jobscout triage   [--limit N] [--min-score N] [--source NAME]
                  [--goal NAME ...] [--all-goals]

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
another without switching,
and `--goal` is **repeatable**: `--goal permanent --goal austria` shows the
intersection (every given goal must match). Definitions stay single-dimension,
the view composes. Goals are **optional**: with no `goals:` block in
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
   (`description_terms`, `description_deny`), **permanence**
   (`permanent_terms` -> `permanent_boost`; a positive-only signal for
   open-ended contracts, shown as a `[permanent]` pill in listings (canonical
   English name; the matched wording stays in the why-breakdown);
   `temporary_terms` marks the inverse with a 0-point `[fixed-term]`, and a
   source may write its parsed contract note into the description), anywhere (`keywords`,
   `exclude_terms`), and **location** (`country_allow`,
   `locations_prefer`, `remote_bonus`). Every point is stored as a reason
   and digests show the exact breakdown
   (`why: title:engineer +3 · stack:python +2 · country:de +2`).
   All scoring terms match whole words and compound parts (so `entwickler` hits
   `Softwareentwickler`, but `git` does not hit `digital`); filter lists
   (`title_deny`, `description_deny`, `exclude_terms`) match whole words only
   (so `sales` does not drop `Salesforce Developer`). `academic_deny` is the
   documented exception: academic-rank titles filter with compound matching
   on purpose, so `Universitätsprofessur` and `W2-Professur` both hit
   `professur`. Stack points only count
   when the title is role-relevant (`stack_needs_title_match`), which keeps
   boilerplate-heavy non-dev listings from inflating. Bands: `min_score`
   (default 6) and `strong_threshold` (default 12); `jobscout stats` prints a
   score histogram whenever you want to recalibrate.
   `jobscout rescore` re-applies edited preferences to everything already stored
   without re-fetching, and `jobscout scan` does that automatically when it
   notices the preferences changed since the last run. New matches whose
   permanence is not yet visible get their detail page completed once
   (`detail_probe_per_scan`, default 10 per run) and are rescored, so the
   permanence boost applies to every match rather than only to near-misses;
   the scan prints `promoted` when the completion pushes a row over
   `strong_threshold`.
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

## Restoring a backup

If jobscout starts and finds no workspace, it looks for a backup first. That is
the state you land in after losing or removing `~/.jobscout`, and a backup
sitting in `~/jobscout-backups` used to go unclaimed while the app said only
"no config found". Now it shows what it found — date, posting count, how many
you had marked — and offers to restore it, or lets you start fresh.

From a terminal:

```bash
jobscout restore                     # restore the newest backup
jobscout restore --from FILE         # a particular one
jobscout restore --overwrite --yes   # replace a live workspace
```

Two safety rules, both of them a response to actually losing a workspace:

- It **refuses to overwrite** a workspace that already holds a config or a
  database. Restoring an old snapshot over live triage decisions is worse than
  leaving the backup alone, so that needs `--overwrite`.
- When it does overwrite, it **takes a fresh backup of what it is replacing
  first**, and tells you where that went. A restore you regret has a way back.

## Updating

The window checks GitHub once a day for a newer release. If there is one, and
you have not turned it off, **it installs itself**: the archive is downloaded
and verified, and jobscout restarts on the new version. The notice bar says what
is happening while it does.

That happens **at startup**, which is the one moment nothing is half-done. The
window has just opened and you have not started anything yet, so a restart
cannot interrupt work.

Both halves are settings, in the config as `updates.check` and `updates.auto`,
or as the two switches under **Updates** in the app. Turning off `auto` gets you
the two explicit steps below; turning off `check` stops jobscout asking GitHub
at all. `jobscout update --check` still works either way — asking for a check
yourself is the instruction, so the command does not consult the setting.

The two explicit steps, which is what you get with automatic installing off:

1. **Install and restart** downloads and verifies. jobscout keeps running.
2. The button becomes **Quit and update**. Pressing it terminates jobscout, the
   new build takes its place, and jobscout reopens by itself. **Not now** leaves
   the verified download on disk, so coming back later does not fetch it again.

An automatic attempt that cannot complete hands itself back as these two steps,
with the reason, rather than failing silently.

The download is verified before anything is replaced:

1. the archive is checked against the **sha256 GitHub publishes for that
   asset**, so a truncated or corrupted download is refused;
2. it is unpacked and confirmed to actually contain a jobscout build;
3. a small helper waits for jobscout to exit, moves the old build aside, swaps
   the new one in, and relaunches. If the swap fails it **restores the previous
   build** and starts *that*, so an update can never leave you with nothing that
   launches — which matters most when nobody was watching.

Nothing else can terminate the app as a side effect, and neither step touches
`~/.jobscout/`: your config, database and backups are not part of the swap.

The helper waits for a *clean exit*, not for you: it swaps within a second of
the process ending, with nothing to click. The wait is bounded (120s) so a
helper that somehow outlives a stuck app gives up and leaves your install alone
rather than hanging.

`jobscout update --from FILE` installs a build you already have (a zip or
tar.gz from the release page, or one you built yourself) using the same checks
and the same swap. That is the path to use if the release host is unreachable,
or if you would rather download it yourself.

**Updates do not hit the Gatekeeper prompt.** A browser sets macOS's quarantine
flag on what it downloads, which is why a first install needs the "Open Anyway"
dance in `docs/INSTALL.md`. The in-app updater downloads over HTTPS itself and
does not set that flag, so an updated build starts normally. Downloading the
release by hand in a browser and using `--from` will prompt.

**Nothing here is signed.** The download is verified against the hash GitHub
returns for it, which protects against corruption in transit — it is not proof
of who published the file, because there is no signature to check. See
`docs/INSTALL.md` for what each platform shows on a manual install.

## Sources

Working today (all verified live, all free). The `market` column is the same
grouping the Settings screen uses, so the two read the same way:

| source | market | how |
|---|---|---|
| `stepstone` | DACH | Job search — server-rendered result list |
| `xing` | DACH | Job search (public pages, server-rendered) |
| `stellenanzeigen` | DACH | Job search (server-rendered Next.js pages) |
| `karriere_at` | DACH | Result list lives in window.VUE_INITIAL_STATE |
| `jobs_ch` | DACH | Public search API, JSON |
| `arbeitnow` | DACH | Free public job board API (Germany-friendly, global remote) |
| `berlinstartupjobs` | DACH | WordPress RSS feed of Berlin startup roles |
| `ams` | DACH | The Austrian public employment service |
| `jobsat` | DACH | Austria's largest job board, server-rendered search results |
| `germantechjobs` | DACH | RSS feed of German IT roles (germantechjobs.de/rss) |
| `swissdevjobs` | DACH | The Swiss sibling of GermanTechJobs (same RSS platform) |
| `jobup` | DACH | Public search API, JSON |
| `jobscout24` | DACH | Server-rendered search, no key |
| `jobware` | DACH | German job board, server-rendered (Qwik) |
| `talent` | DACH | Global job aggregator with a German front (de.talent.com) |
| `englishjobs` | DACH | English-speaking jobs in Germany |
| `themuse` | US | Public jobs API, no key required |
| `usajobs` | US | US federal job announcements (requires a free Authorization-Key) |
| `governmentjobs` | US | US public-sector job board, server-rendered |
| `eures` | EU | The EU's official job vacancy portal (public JSON search API) |
| `euraxess` | EU | The EU's official researcher-jobs portal (all disciplines) |
| `linkedin` | global / remote | Public guest search — no login, no API key |
| `hn` | global / remote | Hacker News 'Ask HN: Who is hiring?' — latest monthly thread via the public Algolia + Firebase APIs |
| `remotive` | global / remote | Free public API of remote jobs |
| `wwr` | global / remote | RSS feeds per category |
| `remoteok` | global / remote | Public JSON feed |
| `larajobs` | global / remote | RSS feed of Laravel/PHP roles |
| `workingnomads` | global / remote | Public jobs API (remote roles) |
| `nodesk` | global / remote | RSS feed of remote roles |
| `himalayas` | global / remote | Public jobs API (global remote roles, cursor paginated) |
| `adzuna` | global / remote | Multinational job-search API (requires a free app_id + app_key) |
| `jobicy` | global / remote | Free public remote-jobs API (no key) |
| `wordpressjobs` | global / remote | The official WP job board, RSS |
| `pythonjobs` | global / remote | The PSF job board, RSS |
| `fossjobs` | global / remote | Free/open-source software jobs, RSS |
| `academics` | academic | Germany's academic jobs board (ZEIT group) |
| `wissenschaftskarriere` | academic | Academic jobs portal (DE/AT/CH) |
| `myscience` | academic | Science jobs in Germany, Switzerland and Austria |
| `greenhouse` | your employers | Company boards — public job board API |
| `ashby` | your employers | Company boards — public posting API |
| `recruitee` | your employers | Public company boards API (`<host>/api/offers/`) |
| `lever` | your employers | Company boards — public postings API |
| `smartrecruiters` | your employers | Company boards — public postings API (+ detail for descriptions) |
| `workable` | your employers | Company boards — public widget API |
| `workday` | your employers | CXS — the per-company job API behind Fortune 500 careers sites |
| `teamtailor` | your employers | Per-company boards with a public JSON Feed |
| `join` | your employers | DACH SMB ATS with public per-company boards |

The two keyed sources ship **disabled**. Enable them in Settings once you have a
key. If you enable one without a key, the scan says so by name and prints the
signup URL, rather than reporting zero jobs and leaving you to guess.

Known limitations, on purpose:

- **The big US boards are closed.** Verified with live requests, not assumed:
  Indeed's API is gone and its RSS is retired (search itself 403s), Monster's
  API host is NXDOMAIN, CareerBuilder's gateway answers 573 with its backend
  DNS unresolvable, ZipRecruiter and Dice are partner-only (Dice's API hosts no
  longer resolve), Built In serves no feed despite nine probes, Wellfound sits
  behind Cloudflare Turnstile, Otta merged into Welcome to the Jungle, and
  Jooble's REST API is behind a Cloudflare challenge — the same wall that got
  himalayas dropped from this project. `themuse` and `workday` are what is
  genuinely reachable for a US search; between them they cover a US job board
  and the Fortune-500 careers sites.
- **Indeed**: actively blocks scripted access ("Security Check"), no public API
  without tokens. Not supported; StepStone/XING/stellenanzeigen cover the same
  German market.
- **Bundesagentur für Arbeit**: its jobsuche API now rejects anonymous requests
  (403) and needs a free developer registration with OAuth client credentials.
  If you register, the adapter is a small addition — not included by default to
  keep the "no accounts" promise.
- **willhaben (AT)**: job board returns 404 for scripted access; karriere.at
  covers Austria.
- **Newly measured walls (2026-10-15)**: kimeta.de and its white-label
  jobs.heise.de answer an anti-bot check on `/search` (client-rendered
  results; do not pursue), hiring.cafe answers 403, remote.co fails TLS in
  every variant, and join.com's sitemap files are Cloudflare-403 while its
  board pages stay open.
  The academic boards jobvector.de, hochschul-jobs.de and
  science-jobs-de.de answer the same interactive check to plain HTTP;
  the reachable academic sources are `academics`,
  `wissenschaftskarriere`, `euraxess` and `myscience`.
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
│   ├── web/         # the window's HTML/CSS/JS (bundled as data)
│   ├── app.py       # desktop shell (pywebview) -> jobscout-app
│   ├── api.py       # the service layer the window and the CLI both use
│   ├── cli.py       # jobscout <command>
│   ├── scanner.py   # the one scan loop (CLI and window share it)
│   ├── cfgwrite.py  # comment-preserving edits to config.yaml
│   ├── backup.py    # archive / restore the workspace
│   ├── update.py    # check for, verify and install a newer build
│   ├── querygen.py  # build search queries from the profile
│   ├── score.py     # the rule engine
│   ├── salary.py    # pay parsing + formatting
│   ├── liveness.py  # dead-listing checks (ATS-aware)
│   ├── companies.py # ATS detection + config edits
│   ├── triage.py    # the one-by-one review loop
│   ├── digest.py    # markdown digest writer
│   └── db.py        # sqlite storage + dedup
├── packaging/       # PyInstaller spec + frozen-app entry point
├── .github/         # CI: test gate + per-OS bundle matrix
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

The parts added since are tested against the damage they exist to prevent:
`cfgwrite` must leave a same-value write byte-identical and must not lose a
comment (including inline trailing ones), writes are serialised and atomic so
concurrent edits cannot corrupt the file, `backup` is verified by *restoring*
it and checking the state comes back, `restore` must refuse to clobber a live
workspace and must leave a safety copy that outlives the call, and `update`
verifies a checksum before unpacking, refuses anything that is not a jobscout
build, and restores the previous build when a swap fails.

## Building a distributable

The desktop app is bundled with [PyInstaller](https://pyinstaller.org) from
`packaging/jobscout.spec`. PyInstaller **cannot cross-compile** — a build can
only produce artifacts for the OS and CPU it runs on — so a macOS build produces
only macOS artifacts, and cross-platform output needs one runner per target. That
is what the CI matrix below exists for.

```bash
uv sync --extra app
uv pip install "pyinstaller>=6.22,<7"     # build tool, not a runtime dependency
uv run --no-sync pyinstaller packaging/jobscout.spec
```

Output: `dist/jobscout/` (a onedir bundle; the executable inside it is
`jobscout`) and, on macOS, `dist/jobscout.app`. `--noconfirm` overwrites an
existing `dist/`, `--clean` drops the cached analysis in `build/`. The bundle is
the window only — the `jobscout` CLI is not included in it.

What the spec puts in, and why it matters:

| bundled | reason |
|---|---|
| the `jobscout` package | the app itself |
| `jobscout/web/*` (`index.html`, `app.js`, `styles.css`) | loaded from disk at runtime (`app.py`); miss it and the window is blank |
| `geonamescache` data (`cities*.json`) | offline location resolution (`geo.py` tier 2) |
| `certifi`'s CA bundle | the TLS context used for `europa.eu` sources |
| `hiddenimports` for all 47 `jobscout.sources.*` adapters | they are imported by name at runtime, so static analysis cannot see them |
| the pywebview backend for the build platform | also resolved by name at runtime (`webview/guilib.py`) |

**Never bundled:** `~/.jobscout/config.yaml`, `~/.jobscout/jobscout.db` and
`reports/` — user data lives in your home directory and a fresh install creates
nothing, so run `jobscout init` once (see *Desktop app* above) before the first
launch. The spec refuses to build if the source adapters and its own list drift
apart, rather than shipping a bundle that loads no sources.

### CI artifacts (`.github/workflows/build.yml`)

| runner | artifact | notes |
|---|---|---|
| `macos-latest` | `jobscout-macos-arm64.zip` | Apple silicon, `.app` inside |
| `macos-15-intel` | `jobscout-macos-x86_64.zip` | Intel, `.app` inside |
| `ubuntu-22.04` | `jobscout-linux-x86_64.tar.gz` | x86_64 |
| `ubuntu-24.04-arm` | `jobscout-linux-arm64.tar.gz` | arm64 |
| `windows-latest` | `jobscout-windows-x86_64.zip` | x86_64 |

Push a `v*` tag or start the workflow by hand (Actions → *build* → *Run
workflow*): the test job runs first, then each runner builds natively and uploads
its archive. Pull requests run the test job only, so they stay fast. Artifacts
expire with the normal GitHub retention window and are rebuilt from source —
they are not releases.

A `v*` **tag** goes one step further and publishes the release directly, with no
draft to remember to click. Its notes are the commits since the previous tag,
grouped into features, fixes and documentation by the prefix each commit already
carries (`packaging/changelog.sh`), followed by the install table
(`packaging/release-footer.md`). The version has to be right *before* the tag:
it is read from `pyproject.toml`, and an app whose version does not move will
never see its own update.

### Installing an artifact

**macOS** (first launch is blocked: these builds are unsigned, see below):

```bash
ditto -x -k jobscout-macos-arm64.zip .   # ditto, not unzip: keeps the .app intact
mv jobscout.app /Applications/
```

Try to open it, get blocked, click **Done** (not "Move to Trash"), then
**System Settings → Privacy & Security → scroll to Security → Open Anyway** and
confirm with your password. Two things about that button: it only appears for
roughly an hour after the failed launch and only for the most recently blocked
app, so if it is missing, launch the app again to re-trigger the block. If macOS
instead says the app *"is damaged and can't be opened"*, that is the quarantine
flag, not a broken build — `xattr -cr /Applications/jobscout.app` clears it.
Each re-download is a new copy, so the prompt comes back every time.

**Windows:** unzip, run `jobscout.exe`; SmartScreen says "Windows protected your
PC" → **More info** → **Run anyway**. Also every new download. WebView2 is
required and is preinstalled on Windows 10 (1803+) and Windows 11; if the window
never appears, the runtime is the first thing to check. On a clean Windows 11
install Smart App Control can block unsigned executables with no "Run anyway"
button at all — that is a Windows setting, not something the build can avoid.

**Linux:** `tar -xzf jobscout-linux-x86_64.tar.gz && ./jobscout/jobscout`. No
signing step exists there; the bundle expects the usual desktop libraries
(X11/Wayland, a working graphics stack) and it is by far the largest artifact,
because Qt WebEngine travels with it. The x86_64 build is built against glibc
2.34 and the arm64 one against glibc 2.39 — that is the oldest arm64 Qt WebEngine
wheel Qt publishes — so the arm64 tarball needs Ubuntu 24.04 or newer to run.

**No code signing or notarization is wired into CI — that is deliberate.** macOS
needs an Apple Developer ID and Windows needs a certificate; both are handled
separately from this workflow, so the artifacts above are unsigned by design.
