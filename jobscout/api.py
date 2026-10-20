"""Headless service layer — the single contract shared by the CLI and the GUI.

Why this exists: the GUI must not call `cmd_*` functions (they take an
argparse.Namespace, print to stdout, and load the config implicitly), and it
must not re-implement any rule. Everything here returns plain data and defers
to the existing core, so `score.py` / `geo.py` / `salary.py` / `sources/*` stay
the only place the logic lives.

Nothing in this module prints. Config writes go through `cfgwrite`, which
edits the file line-wise so the 137 comment lines documenting it survive —
see that module for why a yaml round-trip is not an option.
"""
from __future__ import annotations

import json
import pathlib
import statistics
from datetime import date, datetime, timedelta, timezone

from . import contract
from . import db as dbm
from . import deadlines
from . import salary as salmod
from . import sources

VALID_STATUSES = ("new", "saved", "applied", "dismissed")


def _cell(row, name: str) -> str:
    """One column as text from either a sqlite3.Row or a plain dict.

    Missing means empty: a row from an older DB, or a test's hand-made dict, must
    read as "we do not know" rather than blowing up the whole view.
    """
    try:
        value = row[name]
    except (KeyError, IndexError, TypeError):
        return ""
    return str(value or "")


def posting_age_days(row, now=None) -> float | None:
    """How long ago this posting was published, in days, or None if unknown.

    `posted_at` is the board's own date and is the right clock when it exists
    (every source that provides one writes `YYYY-MM-DD`). When it is missing the
    honest fallback is `first_seen_at`, when WE first saw it, which can only
    make a posting look younger than it is -- never older. A date in the future
    (some boards pre-publish, karriere_at does) clamps to 0 rather than going
    negative and reading as "fresher than new".
    """
    raw = _cell(row, "posted_at").strip() or _cell(row, "first_seen_at").strip()
    if not raw:
        return None
    for parse in (lambda s: datetime.fromisoformat(s),
                  lambda s: datetime.fromisoformat(s[:10])):
        try:
            when = parse(raw.replace("Z", "+00:00"))
            break
        except (ValueError, TypeError):
            continue
    else:
        return None
    now = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (now - when).total_seconds() / 86400.0)


def republished_as(conn, pid: int, source: str = "") -> list[dict]:
    """Boards that carried the same role AFTER we first saw it.

    A row whose `dup_of` points here is the same company+title+location arriving
    later, so this is the answer to "has it gone public yet": an empty list means
    the employer's own board is still the only place we know it from.

    Twins from the SAME board are left out, and that is not pedantry: arbeitnow
    reposts a role under a new slug, so a row there can be its own duplicate.
    Measured on a real store, most dup rows are exactly that. Telling the user
    "also on arbeitnow" while they are looking at arbeitnow would be a lie about
    the one thing this feature exists to say.
    """
    out = []
    for r in conn.execute(
            "SELECT p.source AS twin_source, p.first_seen_at, p.url, "
            "       parent.source AS parent_source "
            "FROM postings p JOIN postings parent ON parent.id = p.dup_of "
            "WHERE p.dup_of=? ORDER BY p.first_seen_at", (int(pid),)):
        if source and r["twin_source"] == source:
            continue
        if r["twin_source"] == r["parent_source"]:
            continue
        out.append({"source": r["twin_source"], "seen_at": r["first_seen_at"],
                    "direct": sources.is_direct(r["twin_source"]),
                    "url": r["url"] or ""})
    # A row that is itself a duplicate also needs a pill, naming the board it
    # was republished FROM. Only the canonical row used to carry one, so the
    # XING copy of a role looked like an unrelated single listing.
    own = conn.execute(
        "SELECT p.source AS own_source, parent.source AS parent_source, "
        "       parent.first_seen_at, parent.url "
        "FROM postings p JOIN postings parent ON parent.id = p.dup_of "
        "WHERE p.id=?", (int(pid),)).fetchone()
    if own and own["parent_source"] and own["parent_source"] != own["own_source"] \
            and not (source and own["parent_source"] == source):
        out.insert(0, {"source": own["parent_source"], "seen_at": own["first_seen_at"],
                       "direct": sources.is_direct(own["parent_source"]),
                       "url": own["url"] or "", "parent": True})
    return out


def republished_map(conn, rows: list[tuple[int, str]]) -> dict[int, list[dict]]:
    """`republished_as` for many rows at once, so the grid is not N+1 queries.

    `rows` is (id, source) pairs -- the source is needed to drop a board's
    duplicates of its own listing.
    """
    found: dict[int, list[dict]] = {}
    pairs = [(int(i), str(s or "")) for i, s in rows]
    for start in range(0, len(pairs), 500):        # SQLite's parameter limit
        chunk = pairs[start:start + 500]
        if not chunk:
            continue
        marks = ",".join("?" * len(chunk))
        src_of = dict(chunk)
        for r in conn.execute(
                f"SELECT p.dup_of, p.source AS twin_source, p.first_seen_at, p.url, "
                f"       parent.source AS parent_source "
                f"FROM postings p JOIN postings parent ON parent.id = p.dup_of "
                f"WHERE p.dup_of IN ({marks}) ORDER BY p.first_seen_at",
                [i for i, _ in chunk]):
            dup_of = int(r["dup_of"])
            if r["twin_source"] == r["parent_source"] or r["twin_source"] == src_of.get(dup_of):
                continue
            found.setdefault(dup_of, []).append(
                {"source": r["twin_source"], "seen_at": r["first_seen_at"],
                 "direct": sources.is_direct(r["twin_source"]), "url": r["url"] or ""})
        for r in conn.execute(
                f"SELECT p.id, p.source AS own_source, parent.source AS parent_source, "
                f"       parent.first_seen_at, parent.url "
                f"FROM postings p JOIN postings parent ON parent.id = p.dup_of "
                f"WHERE p.id IN ({marks})", [i for i, _ in chunk]):
            pid = int(r["id"])
            if not r["parent_source"]:
                continue
            if r["parent_source"] == r["own_source"] or r["parent_source"] == src_of.get(pid):
                continue
            found.setdefault(pid, []).insert(0, {
                "source": r["parent_source"], "seen_at": r["first_seen_at"],
                "direct": sources.is_direct(r["parent_source"]),
                "url": r["url"] or "", "parent": True})
    return found


def posting_dict(row, twins: list[dict] | None = None) -> dict:
    """One stored posting as a JSON-safe dict.

    `reasons` is stored as JSON *text* in the DB; the UI needs the parsed list
    to render its "why" chips. A malformed value yields `[]` rather than raising,
    because a hand-edited or partially-migrated DB must not break the whole view.

    `direct` / `direct_note` / `age_days` / `republished` are what the card needs
    to say "this is fresh from the employer's board and nobody else has it",
    which is the only version of an insider tip this data can honestly support.
    """
    out = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else dict(row)
    raw = out.get("reasons") or ""
    try:
        parsed = json.loads(raw) if raw else []
        out["reasons"] = parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        out["reasons"] = []
    # One word for the fact, whatever language the advert used; the matched
    # phrase stays in the reasons, which is where the reading can be checked.
    out["contract"] = contract.from_reasons(out["reasons"])
    try:
        out["salary"] = salmod.fmt_row(row) or ""
    except Exception:                                        # noqa: BLE001
        out["salary"] = ""
    out["score"] = float(out.get("score") or 0.0)
    out["direct"] = sources.is_direct(out.get("source") or "")
    out["direct_note"] = sources.direct_note(out.get("source") or "")
    out["age_days"] = posting_age_days(row)
    out["republished"] = list(twins or [])
    # Deadlines are worded in Python, once, so the card, the pane and the
    # summary cannot disagree about what "closes in 3 days" means.
    when = _cell(row, "deadline_at").strip()
    out["deadline_text"] = deadlines.describe(when) if when else ""
    # Same definition the wording uses, so a card cannot say "closes in 3 days"
    # next to a count that disagrees (see deadlines.days_until).
    out["closes_in_days"] = deadlines.days_until(when) if when else None
    return out


def list_matches(conn, *, min_score=None, status=None, source=None,
                 limit=None) -> list[dict]:
    """Stored postings that survived the rule engine, best first.

    Excludes filtered rows and duplicates by default so the count in the window
    matches the count in the digest.

    Holds dbm.LOCK: this runs on pywebview worker threads, and a read
    interleaving with a concurrent `mark_posting` write is a real race.
    """
    with dbm.LOCK:
        rows = dbm.list_postings(conn, min_score=min_score, status=status,
                                 source=source, limit=limit or 100000)
        keep = [r for r in rows if not r["filtered"] and r["dup_of"] is None]
        # one query for every republished twin on the page, not one per card
        twins = republished_map(conn, [(r["id"], r["source"]) for r in keep])
        out = [posting_dict(r, twins.get(r["id"])) for r in keep]
        # The advert text is two thirds of this payload (measured: 0.89MB of
        # 1.33MB for 400 rows) and the card never renders it -- the detail pane
        # fetches it when a card is opened. Sending it here is what forced the
        # 400-row cap, and the cap was hiding the freshest postings, because
        # the rows are sorted by score and a fresh advert usually scores low.
        for d in out:
            d.pop("description", None)
    out.sort(key=lambda r: r["score"], reverse=True)
    if limit:
        out = out[:limit]
    return out


def get_posting(conn, pid: int) -> dict | None:
    with dbm.LOCK:
        row = dbm.get_posting(conn, pid)
    return posting_dict(row) if row is not None else None


def mark_posting(conn, pid: int, status: str) -> dict:
    """Set a posting's triage status. Rejects anything not in VALID_STATUSES.

    A GUI can send an arbitrary string, so validate rather than trusting it.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES!r}, got {status!r}")
    with dbm.LOCK:
        dbm.set_status(conn, int(pid), status)
    return {"id": int(pid), "status": status}


def source_counts(conn) -> list[dict]:
    """Per-source row counts, for the source filter dropdown."""
    with dbm.LOCK:
        counts = dbm.counts(conn) or {}
    return [{"source": s, "count": n} for s, n in counts.items()]


def search_text(conn, text: str, limit: int = 400) -> dict:
    """Full text over the advert prose, shaped for the window and the CLI.

    `limited` says the search stopped at the cap rather than at the end of the
    results, so the UI can say "the first N" instead of implying it found N.
    """
    limit = max(1, int(limit))
    hits = dbm.search_text(conn, text, limit=limit)
    return {"query": text, "hits": hits, "count": len(hits),
            "limited": len(hits) >= limit, "limit": limit}


def _percentiles(values: list[float]) -> dict | None:
    """Quartiles of a list of numbers, in the units they came in."""
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        one = round(values[0])
        return {"p25": one, "median": one, "p75": one, "min": one, "max": one}
    quart = statistics.quantiles(values, n=4, method="inclusive")
    return {"p25": round(quart[0]), "median": round(quart[1]), "p75": round(quart[2]),
            "min": round(values[0]), "max": round(values[-1])}


def salary_report(conn, *, words=(), city: str = "", days: int = 0,
                  limit: int = 20000) -> dict:
    """What the postings in this store advertise, per year.

    Reported rather than sold, and with its own caveats attached, because a pay
    figure is the easiest thing here to mislead with: only 16% of postings carry
    a number at all, and the boards that publish one are not a random sample.
    The count, the coverage and the sources are part of the answer, not fine
    print -- a median over nine postings from one board is a different claim
    from a median over two thousand.

    Currencies are NOT mixed: EUR and CHF in one distribution would be a
    meaningless number, so the biggest group is reported and the rest counted.

    `days` (when given) keeps only postings with a date inside that window, and
    only those whose date is known -- a store two weeks old cannot say anything
    about a year, and saying so is better than quietly including everything.
    """
    from . import salary as salmod
    where, args = ["dup_of IS NULL", "filtered = 0"], []
    for w in words:
        w = str(w).strip().lower()
        if w:
            where.append("lower(title) LIKE ?")
            args.append(f"%{w}%")
    if city:
        where.append("lower(location) LIKE ?")
        args.append(f"%{str(city).strip().lower()}%")
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).date().isoformat()
        where.append("posted_at != '' AND posted_at >= ?")
        args.append(cutoff)

    rows = conn.execute(
        f"SELECT source, salary_annual_max, salary_min, salary_period, salary_currency,"
        f" posted_at, location FROM postings WHERE {' AND '.join(where)} "
        f"LIMIT ?", (*args, int(limit))).fetchall()

    by_currency: dict[str, list[float]] = {}
    mins_by_currency: dict[str, list[float]] = {}
    by_source: dict[str, list[float]] = {}
    for r in rows:
        top = r["salary_annual_max"]
        if top is None:
            continue
        cur = (r["salary_currency"] or "").upper() or "?"
        by_currency.setdefault(cur, []).append(float(top))
        low = salmod.annual_min(r)
        if low is not None:
            mins_by_currency.setdefault(cur, []).append(low)
        by_source.setdefault(r["source"], []).append(float(top))

    main = max(by_currency, key=lambda c: len(by_currency[c])) if by_currency else ""
    tops = by_currency.get(main, [])
    sources = sorted(({"source": s, "count": len(v),
                       "median": _percentiles(v)["median"]} for s, v in by_source.items()),
                     key=lambda r: -r["count"])[:6]
    return {
        "asked": {"words": [str(w) for w in words if str(w).strip()],
                  "city": city, "days": int(days or 0)},
        "postings": len(rows),
        "with_salary": sum(len(v) for v in by_currency.values()),
        "currency": main,
        "currencies": {c: len(v) for c, v in sorted(by_currency.items(),
                                                    key=lambda kv: -len(kv[1]))},
        "max_annual": _percentiles(tops),
        "min_annual": _percentiles(mins_by_currency.get(main, [])) if main else None,
        "by_source": sources,
    }


def lead_times(conn) -> dict:
    """What the cross-source pairs say, measured rather than promised.

    The evidence needs no new storage: a role known from two boards exists as
    two rows, each holding the moment WE first saw it on its own board, and the
    later one points at the earlier via dup_of. So the head start is a number in
    the database, and this reports it -- including the honest zero.

    It is reported and not sold because the sample is thin and the gaps are
    contaminated by our own scan rhythm: measured on a real store, 27,357
    postings held 73 roles on more than one source, and a role that had been
    sitting on a board for three weeks before we ever scanned looks exactly like
    a three-week lead. More scans make it mean more; nothing else does.
    """
    out = {"pairs": 0, "comparable": 0, "direct_first": 0, "aggregator_first": 0,
           "median_days": None}
    gaps = []
    for r in conn.execute(
            "SELECT a.source AS earlier, a.first_seen_at AS t0, "
            "       b.source AS later,  b.first_seen_at AS t1 "
            "FROM postings a JOIN postings b ON b.dup_of = a.id "
            "WHERE a.source <> b.source"):
        out["pairs"] += 1
        if sources.is_direct(r["earlier"]):
            out["direct_first"] += 1
        elif sources.is_direct(r["later"]):
            out["aggregator_first"] += 1
        try:
            t0 = datetime.fromisoformat(r["t0"])
            t1 = datetime.fromisoformat(r["t1"])
        except (ValueError, TypeError):
            continue
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        if t1.tzinfo is None:
            t1 = t1.replace(tzinfo=timezone.utc)
        gaps.append((t1 - t0).total_seconds() / 86400.0)
    if gaps:
        # the real median, not the middle ELEMENT: with an even count those
        # differ, and the element version reads as a bigger lag than the data
        out["median_days"] = round(statistics.median(gaps), 1)
    # Of the pairs, only some have one side republishing the other; the rest are
    # two employer boards or two aggregators, where "who was first" is not the
    # question this feature asks. Report the denominator, or "first in 13" reads
    # as 13 out of everything.
    out["comparable"] = out["direct_first"] + out["aggregator_first"]
    return out


def stats(conn, prefs: dict) -> dict:
    """Header numbers plus the score histogram used to calibrate thresholds."""
    min_score = float(prefs.get("min_score", 6))
    strong_th = float(prefs.get("strong_threshold", 10))
    with dbm.LOCK:
        matches, strong = dbm.count_matches(conn, min_score, strong_th)
        run = dbm.get_run(conn)
        hist = [{"score": s, "count": n} for s, n in dbm.score_histogram(conn)]
        lead = lead_times(conn)
    return {
        "matches": matches,
        "strong": strong,
        "min_score": min_score,
        "strong_threshold": strong_th,
        # the window the "fresh from the source" filter uses; the filter itself
        # runs in the window, which already has the rows
        "insider_days": float(prefs.get("insider_days", 7)),
        # the window the "closing soon" filter uses, same reasoning
        "closing_days": float(prefs.get("closing_days", 14)),
        # what the head start has actually measured so far, not what it promises
        "lead": lead,
        "histogram": hist,
        "run": ({k: run[k] for k in run.keys()} if run is not None else None),
    }


# --------------------------------------------------------------- settings

# The editable surface, in the order a settings screen should show it. Each
# entry names the config key it edits and how to render/validate it. A key NOT
# in this list cannot be written through the app: `cfgwrite` refuses unknown
# keys rather than appending them, and this list is the allow-list on top.
SETTINGS = [
    {"key": "sources.enabled", "op": "list", "kind": "str_list",
     "group": "Where to look",
     "label": "Which job boards to scan",
     "options_from": "sources",
     "help": "Untick the ones you do not want. Fewer boards means a faster "
             "scan; anything already stored stays in your list either way."},

    {"key": "preferences.title_terms", "op": "list", "kind": "str_list",
     "group": "What you are looking for",
     "label": "Job titles you want",
     "help": "Each one becomes a search query at the sources. Add the titles you "
             "would actually take."},
    {"key": "preferences.stack_preferred", "op": "mapping", "kind": "str_int_map",
     "group": "What you are looking for",
     "label": "Skills you care about, and how much",
     "help": "Higher weight = more interested. 3 is a strong preference, "
             "1 is a mild one."},
    {"key": "preferences.title_deny", "op": "list", "kind": "str_list",
     "group": "What you are looking for",
     "label": "Titles to reject",
     "help": "Whole-word match against the title. A posting containing any of "
             "these is filtered out."},

    {"key": "preferences.country_allow", "op": "list", "kind": "str_list",
     "group": "Where",
     "label": "Countries to accept",
     "help": "Two-letter codes, for example de for Germany. Empty means "
             "no country filter."},
    {"key": "preferences.locations_prefer", "op": "list", "kind": "str_list",
     "group": "Where",
     "label": "Places you prefer",
     "help": "Postings here score higher. Substring match, so a city name is "
             "usually enough."},

    {"key": "preferences.min_score", "op": "scalar", "kind": "number",
     "group": "How strict",
     "label": "Minimum score to show",
     "help": "Everything below this is stored but hidden from you."},
    {"key": "preferences.strong_threshold", "op": "scalar", "kind": "number",
     "group": "How strict",
     "label": "Score that counts as a strong match",
     "help": "Shown as the gold band. jobscout stats prints the histogram you "
             "need to pick this from data."},
    {"key": "preferences.max_age_days", "op": "scalar", "kind": "number",
     "group": "How strict",
     "label": "Ignore postings older than this",
     "help": "In days."},
    {"key": "preferences.scan_if_older_hours", "op": "scalar", "kind": "number",
     "default": 24,
     "group": "How strict",
     "label": "Look for new openings when the last scan is older than",
     "help": "In hours, 0 to only ever scan when you press the button. This is "
             "what keeps everything time-based honest: freshness, the lead-time "
             "measurement, and a role being re-advertised all need the boards "
             "to be looked at regularly. The window opens and works normally "
             "while it scans."},
    {"key": "preferences.require_title_match", "op": "scalar", "kind": "bool",
     "default": True,
     "group": "How strict",
     "label": "Only match jobs whose title matches your job titles",
     "help": "On by default, and the reason your board stays about your work: "
             "permanent contract, location and keywords are bonuses on a "
             "relevant posting, and with this off they are enough on their own, "
             "which is how a museum curatorship ends up next to a PHP role."},
    {"key": "preferences.closing_days", "op": "scalar", "kind": "number",
     "default": 14,
     "group": "How strict",
     "label": "How close a deadline counts as closing soon",
     "help": "In days. Deadlines are read out of the advert text, and only "
             "from the full advert: most boards publish a teaser that has no "
             "date in it at all, so 'Closing soon' fills up as the scan "
             "completes the advert pages it can reach."},
    {"key": "preferences.max_results", "op": "scalar", "kind": "number",
     "default": 2000,
     "group": "How strict",
     "label": "How many results to load",
     "help": "Best-scored first. The window filters everything it has loaded, "
             "so a low number can hide a match from a filter, and the footer "
             "says when that is happening. 0 loads everything."},
    {"key": "preferences.insider_days", "op": "scalar", "kind": "number",
     "default": 7,
     "group": "How strict",
     "label": "How recent counts as fresh from the source",
     "help": "In days. A posting from the employer's own board (or an "
             "institution's own announcement channel) younger than this is "
             "marked on the card, and 'Fresh from the source' in the filter "
             "bar shows only those. Nobody republishing it yet is part of the "
             "marking, so this is the window in which you are early."},
    {"key": "preferences.query_budget", "op": "scalar", "kind": "number",
     "group": "How strict",
     "label": "How many queries per source",
     "help": "Queries are generated from your titles and skills. Higher finds "
             "more and takes longer."},
    {"key": "preferences.stack_needs_title_match", "op": "scalar", "kind": "bool",
     "group": "How strict",
     "label": "Only count a skill when the title mentions it too",
     "help": "On for a narrow search, off to let the description convince you."},
    {"key": "updates.check", "op": "scalar", "kind": "bool", "default": True,
     "group": "Updates",
     "label": "Check for updates",
     "help": "Ask GitHub whether a newer version exists, at most once a day, "
             "when the app opens. The only thing sent is the request itself."},
    {"key": "updates.auto", "op": "scalar", "kind": "bool", "default": False,
     "group": "Updates",
     "label": "Install updates automatically",
     "help": "Off by default: when this is off you get a notice with an "
             "Install button and decide yourself, which is the right way round. "
             "On, a newer version is downloaded and checksum-checked in the "
             "background and jobscout restarts on it. Either way nothing "
             "replaces the build you have until the download has verified, and "
             "the previous one is kept until the swap succeeds."},
]

# Per-source knobs worth putting in the window. Only a knob whose key is already
# in the config gets a row (cfgwrite will not invent structure, and a control that
# cannot save is worse than no control); the template documents all of them.
#
# (source, key, label, min, max, help)
SOURCE_KNOBS = [
    ("himalayas", "max_pages", "pages per scan", 1, 60,
     "20 jobs per page, and the board rate-limits, so the scan walks the newest "
     "pages only. Ten pages is 200 postings."),
    ("himalayas", "max_age_days", "stop at postings older than (days)", 1, 365,
     "Paging stops once a page brings nothing newer than this."),
    ("xing", "max_pages", "pages per search query", 1, 20,
     "Each query is a search page; more pages means more roles and more time."),
    ("xing", "max_age_days", "stop at postings older than (days)", 1, 365,
     "Stops walking a query's result pages once the roles are older than this."),
    ("stepstone", "max_pages", "pages per search query", 1, 20,
     "Each query is a results page; more pages means more roles and more time."),
    ("stepstone", "max_age_days", "stop at postings older than (days)", 1, 365,
     "Stops walking a query's result pages once the roles are older than this."),
    ("instaffo", "max_pages", "pages per scan", 1, 500,
     "Ten jobs per page, newest first. The whole board is about 90 pages; a "
     "full walk took 58 seconds and found 903 roles."),
]


def _source_knob_specs() -> list[dict]:
    from .sources import REGISTRY
    out = []
    for source, knob, label, _lo, _hi, help_text in SOURCE_KNOBS:
        name = getattr(REGISTRY.get(source), "LABEL", source)
        out.append({
            "key": f"sources.{source}.{knob}", "op": "scalar", "kind": "number",
            "group": "Per source", "source_knob": True,
            "label": f"{name} · {label}", "help": help_text,
        })
    return out


def _all_settings() -> list[dict]:
    """SETTINGS with the per-source knobs slotted in where they belong.

    Right after the board list, because they answer the same question ("where do
    I look, and how hard"), and the window groups by first appearance.
    """
    specs = list(SETTINGS)
    i = next((k for k, s in enumerate(specs) if s["key"] == "sources.enabled"), -1) + 1
    specs[i:i] = _source_knob_specs()
    return specs


ALL_SETTINGS = _all_settings()
_BY_KEY = {s["key"]: s for s in ALL_SETTINGS}
_KNOB_RANGE = {f"sources.{s}.{k}": (lo, hi) for s, k, _l, lo, hi, _h in SOURCE_KNOBS}


def source_options(enabled) -> list[dict]:
    """One row per source for the settings screen's checkbox list.

    Grouped by the market each board serves -- DACH, US, Europe, global/remote,
    academic, and per-company boards -- so "which of these are for Germany" is
    answerable at a glance instead of by knowing 47 board names. Sorted by that
    group order, and registry order within a group (which keeps related boards
    together). This affects DISPLAY only: the order written back to the config
    comes from the snapshot the front-end takes when the pane opens, so grouping
    can never rewrite the user's deliberate scan sequence.
    """
    from .sources import REGISTRY, SOURCE_NAMES, GROUP_ORDER, group_of
    on = set(enabled or [])
    rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    rows = [{"value": n, "label": getattr(REGISTRY[n], "LABEL", n),
             "checked": n in on, "group": group_of(n)} for n in SOURCE_NAMES]
    rows.sort(key=lambda r: rank.get(r["group"], len(rank)))
    return rows


def read_settings(path) -> list[dict]:
    """Current value of every editable setting, ready for a settings screen."""
    from . import cfgwrite
    text = pathlib.Path(path).read_text(encoding="utf-8")
    out = []
    for spec in ALL_SETTINGS:
        row = dict(spec)
        value = cfgwrite.get_value(text, spec["key"])
        if value is None and spec.get("source_knob"):
            # Not in this config: nothing to edit, so do not offer a control
            # that would fail with "key not found" on save.
            continue
        if value is None and "default" in spec:
            # Not in the file: a config written before this setting existed.
            # Show what is actually in effect -- None would render as "off" and
            # tell the user the opposite of the truth.
            value = spec["default"]
        row["value"] = value
        if spec.get("options_from") == "sources":
            row["options"] = source_options(row["value"])
        out.append(row)
    return out


def _edit_adding_section(path, op: str, key: str, value) -> bool:
    """Set one key, adding its documented top-level section if it is missing.

    `cfgwrite` will not invent structure, and rightly so: appending a key to the
    wrong parent silently corrupts a config. The section body is the same text
    the template ships, so an upgraded config and a fresh one do not drift.
    """
    from . import cfgwrite
    from .config import SECTION_BODIES
    try:
        return cfgwrite.edit(path, op, key, value)
    except cfgwrite.ConfigEditError:
        body = SECTION_BODIES.get(key.split(".", 1)[0])
        if body is None:
            raise
        cfgwrite.edit(path, "section", key.split(".", 1)[0], body)
        return cfgwrite.edit(path, op, key, value)


def known_source_names() -> set[str]:
    from .sources import SOURCE_NAMES
    return set(SOURCE_NAMES)


def validate_setting(key: str, value):
    """Check a value from the UI before it reaches the config file.

    The app is a trust boundary: a GUI (or anything speaking this API) can send
    a string where a number belongs, so this validates rather than assuming.
    Returns a normalised value or raises ValueError.
    """
    spec = _BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"not an editable setting: {key!r}")
    kind = spec["kind"]

    if kind == "number":
        if key in _KNOB_RANGE:
            lo, hi = _KNOB_RANGE[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} expects a number")
            if not (lo <= int(value) <= hi):
                raise ValueError(f"{key} must be between {lo} and {hi}")
            return int(value)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} expects a number, got {type(value).__name__}")
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{key} must be a finite number")
        if key.endswith(("min_score", "strong_threshold")) and not (0 <= value <= 100):
            raise ValueError(f"{key} must be between 0 and 100")
        if key.endswith(("max_age_days", "query_budget")) and not (0 <= int(value) <= 3650):
            raise ValueError(f"{key} must be between 0 and 3650")
        # 0 is allowed and means "no ceiling": the app then loads every match,
        # which is slow on a big board but never hides one.
        if key.endswith("max_results") and not (0 <= int(value) <= 200000):
            raise ValueError(f"{key} must be between 0 and 200000 (0 loads everything)")
        if key.endswith("scan_if_older_hours") and not (0 <= int(value) <= 8760):
            raise ValueError(f"{key} must be between 0 and 8760 (0 never scans by itself)")
        if key.endswith("closing_days") and not (0 <= int(value) <= 365):
            raise ValueError(f"{key} must be between 0 and 365")
        return int(value) if key.endswith(("max_age_days", "query_budget", "max_results",
                                           "scan_if_older_hours", "closing_days")) \
            else float(value)

    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} expects true/false")
        return value

    if kind == "str_list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{key} expects a list of strings")
        clean = [v.strip() for v in value if v.strip()]
        if key == "sources.enabled":
            # A name that is not in the registry is skipped with a warning at
            # scan time, so a typo looks like a board that quietly returns
            # nothing. Reject it here where the user can see why.
            known = known_source_names()
            unknown = [v for v in clean if v not in known]
            if unknown:
                raise ValueError(
                    f"not a source this build knows: {', '.join(sorted(unknown))}")
        return clean

    if kind == "str_int_map":
        if not isinstance(value, dict):
            raise ValueError(f"{key} expects an object")
        out = {}
        for k, v in value.items():
            if not isinstance(k, str) or not k.strip():
                raise ValueError("skill names must be non-empty strings")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"weight for {k!r} must be a number")
            out[k.strip()] = int(v)
        return out

    raise ValueError(f"unknown kind {kind!r}")


def write_setting(path, key: str, value) -> dict:
    """Validate, then edit the config file in place, preserving its comments."""
    spec = _BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"not an editable setting: {key!r}")
    clean = validate_setting(key, value)
    changed = _edit_adding_section(path, spec["op"], key, clean)
    return {"key": key, "value": clean, "changed": changed}


# ---------------------------------------------------------------- theme

THEMES = ("system", "light", "dark")


def read_theme(path) -> str:
    """The window's colour scheme.

    Never raises. A missing or unreadable config means the default rather than
    an error: this is a display preference, and it must not be able to stop the
    app from starting.
    """
    from . import cfgwrite
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return "system"
    value = cfgwrite.get_value(text, "ui.theme")
    return value if value in THEMES else "system"


def write_theme(path, mode: str) -> str:
    """Store the theme, adding the `ui:` section when the config predates it."""
    if mode not in THEMES:
        raise ValueError(f"theme must be one of {', '.join(THEMES)}, got {mode!r}")
    _edit_adding_section(path, "scalar", "ui.theme", mode)
    return mode


# ------------------------------------------------------- companies followed

def _company_boards(cfg) -> dict:
    """The `companies:` block, as {board family: [tokens]}. Never raises."""
    raw = getattr(cfg, "raw", None) or {}
    block = raw.get("companies") or {}
    out = {}
    for name, sub in block.items():
        toks = (sub or {}).get("tokens") if isinstance(sub, dict) else None
        out[str(name)] = [str(t) for t in (toks or [])]
    return out


def watchlist(cfg) -> list[dict]:
    """The employers this profile follows, per board family.

    These are the per-company boards: the reason they are the strongest part of
    the search is that they are the employer's own system, so a vacancy shows up
    there the moment it is published and shows up NOWHERE ELSE first. Measured
    on a real store: of 27,357 postings only 73 roles appear on two different
    sources, so what these boards bring is largely unavailable anywhere else.
    """
    from . import sources
    boards = _company_boards(cfg)
    enabled = set((getattr(cfg, "raw", None) or {}).get("sources", {}).get("enabled") or [])
    rows = []
    for name in sorted(boards, key=lambda n: -len(boards[n])):
        label = getattr(sources.REGISTRY.get(name), "LABEL", name) if name in sources.REGISTRY else name
        rows.append({
            "source": name,
            "label": label,
            "known": name in sources.REGISTRY,
            "enabled": name in enabled,
            "tokens": boards[name],
            "count": len(boards[name]),
            "note": sources.direct_note(name),
        })
    return rows


def watch_remove(cfg, source: str, token: str) -> dict:
    """Stop following one employer. Comments in the config survive this."""
    from . import companies as compmod
    if not source or not token:
        return {"ok": False, "error": "need a board and a company"}
    status = compmod.remove_token(cfg.path, source, token)
    return {"ok": status in ("removed", "missing"), "status": status,
            "board": source, "token": token}


def watch_add(cfg, url: str, fetcher=None) -> dict:
    """Follow the employer behind a careers URL.

    Detection is over the URL itself and, when that finds nothing, over the page
    (scripts usually embed the board link). Nothing is added until the board's
    own API answers with jobs, because a token that validates to an empty board
    looks exactly like a company with no vacancies -- and the user would never
    find out which one it was.
    """
    from . import companies as compmod
    from .http import Fetcher
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "paste a careers page or job listing URL"}
    if not url.startswith("http"):
        url = "https://" + url
    fetcher = fetcher or Fetcher()

    candidates = compmod.detect("", url)
    page_note = ""
    if not candidates:
        try:
            status, body = fetcher.get(url)
        except Exception as e:                                # noqa: BLE001
            return {"ok": False, "error": f"could not fetch that page: {e}"}
        if status != 200:
            return {"ok": False, "error": f"could not fetch that page (HTTP {status})"}
        candidates = compmod.detect(body, url)
        page_note = "found on the page, not the URL"
    if not candidates:
        return {"ok": False, "candidates": [],
                "error": "no Greenhouse / Ashby / Lever / Workable / "
                         "SmartRecruiters / Teamtailor board on that page",
                "hint": "open the careers page, click into a role, and paste the "
                        "URL of the job listing itself"}

    tried = []
    for src, tok in candidates:
        ok, detail = compmod.validate(fetcher, src, tok)
        tried.append({"board": src, "token": tok, "ok": bool(ok), "detail": detail})
        if not ok:
            continue
        status = compmod.add_token(cfg.path, src, tok)         # writes the config
        enabled = compmod.enable_source(cfg.path, src)
        return {"ok": True, "board": src, "token": tok, "added": status,
                "enabled": enabled, "note": page_note, "candidates": tried}
    return {"ok": False, "candidates": tried,
            "error": "that board did not answer with any jobs, so nothing was added"}


# ---------------------------------------------------------------- detail

def show_posting(conn, cfg, pid: int, *, refresh: bool = False) -> dict | None:
    """One posting with everything the detail pane shows.

    Fetches the full description live when asked to (or when the stored one is
    too short to be worth showing), because many boards put only a teaser in
    the listing and the real text behind a second request.
    """
    from . import detail as detailmod
    from .http import Fetcher
    from .models import Posting
    from .sources import REGISTRY

    with dbm.LOCK:
        row = dbm.get_posting(conn, pid)
    if row is None:
        return None

    desc = row["description"] or ""
    note = ""
    if refresh or len(desc) < 400:
        mod = REGISTRY.get(row["source"])
        fetcher = Fetcher()
        posting = Posting(source=row["source"], title=row["title"],
                          company=row["company"], location=row["location"],
                          url=row["url"], external_id=row["external_id"])
        fresh = None
        try:
            fn = getattr(mod, "fetch_detail", None) if mod else None
            if fn is not None:
                fresh = fn(posting, fetcher)
            elif (row["url"] or "").startswith("http"):
                fresh = detailmod.html_detail(row["url"], fetcher)
        except Exception:            # a detail fetch must never break the pane
            fresh = None
        if not fresh:
            # Nothing came back, and the pane cannot just show an empty box:
            # "withdrawn", "no link" and "this board publishes no text" look
            # identical otherwise, and one of them means stop looking. This is
            # the failure path only, so it costs a request only when there is
            # nothing to show anyway.
            try:
                alt, why = detailmod.html_detail_verbose(row["url"], fetcher)
            except Exception:                                    # noqa: BLE001
                alt, why = "", ""
            if alt and len(alt) > len(desc):
                fresh = alt
            else:
                note = why
        if fresh and len(fresh) > len(desc):
            with dbm.LOCK:
                dbm.set_description(conn, row["id"], fresh)
            desc = fresh
            note = ""

    out = posting_dict(row, republished_as(conn, row["id"], row["source"]))
    out["description"] = desc
    # What to say when there is no text to show. Already-known deadness is
    # free; the rest is decided above. Never a silent empty box.
    if not desc:
        liveness = row["verify_note"] if row["verify_status"] == "dead" else ""
        out["description_note"] = note or liveness or (
            "This board did not publish a description for this listing. "
            "Open it on the site to read the advert.")
    out["salary"] = {
        "min": row["salary_min"], "max": row["salary_max"],
        "currency": row["salary_currency"], "period": row["salary_period"],
        "annual_max": row["salary_annual_max"], "raw": row["salary_raw"],
        "text": salmod.fmt_row(row),
    }
    out["liveness"] = {"status": row["verify_status"], "note": row["verify_note"],
                       "checked_at": row["verified_at"]}
    return out
