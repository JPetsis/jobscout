"""The scan loop, extracted so the CLI and the desktop app share one path.

WHY EXTRACTED: the scan was inline in `cli.cmd_scan` and printed as it went, so
the only way to scan was from a terminal. The app needs the same loop with
progress events instead of prints. Copying the loop would have given two
implementations that drift — the CLI would get a fix and the app would not.

So the loop lives here and reports through an `on_event` callback. The CLI
renders those events as its existing output; the app renders them as progress.
One implementation, two views.

Cancellation is cooperative and checked only at source boundaries: a source
mid-fetch cannot be interrupted safely, but the run stops before starting the
next one, which is what "cancel" needs to mean here.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import db as dbm
from . import deadlines
from . import salary as salmod
from .config import source_settings
from .http import FetchError
from .score import Score, contract_label_from_reasons, prefs_hash, score_posting
from .sources import DIRECT_SOURCES, iter_sources
from .textutil import clean_html_lines

Event = dict
OnEvent = Callable[[Event], None]
Cancel = Callable[[], bool]


def _maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher, budget: dict,
                        dup, deadline_budget: dict | None = None) -> Score | None:
    """Complete the detail page of a match once, then rescore.

    WHY: a match may only LOOK weaker than it is because its contract type
    lives on the detail page -- measured on the Uni-Leipzig developer job,
    whose myScience teaser does not say "unbefristet" while its detail page
    states "Befristung unbefristet".

    POLICY: EVERY match (score >= min_score) whose permanence is not yet
    visible gets completed, deliberately including rows already at or above
    strong_threshold. The point is not only to tip near-misses over the
    line: the permanence boost must apply CONSISTENTLY, so a permanent 13
    outranks a fixed-term 12 instead of the ordering depending on whether
    the contract happened to be in the teaser. Rows below min_score are not
    probed -- they are not displayed, and a dev title virtually always
    carries a title-term hit that already puts it over that line.

    Bounded by `detail_probe_per_scan` per run and once per posting ever
    (detail_fetched_at gate; a failed fetch is retried on a later scan).
    Returns the new Score when the rescore changed it.
    """
    if sc.filtered or dup is not None:
        return None
    if sc.score < float(cfg.prefs.get("min_score", 6) or 0):
        return None
    row = conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()
    if row is None or row["detail_fetched_at"]:
        return None
    fn = getattr(mod, "fetch_detail", None)
    if fn is None:
        return None

    # Two reasons to spend a fetch, and two budgets so they do not starve each
    # other: the contract type (which changes the score) and the deadline (which
    # lives in the advert text and nowhere else). Measured on this store, only
    # 0.25% of stored teasers state a deadline, while 8 of 14 FULL academic
    # adverts do -- so the fetch is the whole feature for an application date.
    wants_permanence = not any(str(label).startswith("perm:") for label, _ in sc.reasons)
    wants_deadline = bool(deadline_budget) and not (row["deadline_at"] or "")
    if not (wants_permanence or wants_deadline):
        return None
    spend = budget if wants_permanence else deadline_budget
    if spend.get("left", 0) <= 0:
        return None
    spend["left"] -= 1
    probe = dbm.row_to_posting(row)
    try:
        desc = fn(probe, fetcher)
    except Exception as e:              # a probe must never kill the run
        fetcher.errors.append(f"{p.source}: detail probe failed: "
                              f"{type(e).__name__}: {e}")
        return None
    if probe.early_applicant:
        # The source read the badge off the page it already fetched.
        dbm.set_early(conn, pid)
    if not desc:
        return None
    # A fetch that says no more than the text already stored is a failed probe,
    # not a shorter advert. Without this, a bad fetch replaced a 4,700-character
    # advert with a 242-character teaser, stamped the row as done (so it was
    # never retried) and froze its score at the teaser's points: the ALDI SUD
    # PHP/Laravel role read 14 from XING and 19 from stellenanzeigen.de.
    if not dbm._prefer_fresh(desc, row["description"] or ""):
        return None
    dbm.set_description(conn, pid, desc)
    _note_deadline(conn, pid, desc)
    row = conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()
    sc2 = score_posting(cfg.prefs, dbm.row_to_posting(row))
    # Reasons count as change too: a 0-point marker (tmp:befristet) moves
    # no score but must be stored and reported; a score-only comparison
    # silently dropped it and the pill never appeared.
    if (sc2.score == sc.score and sc2.reasons == sc.reasons
            and bool(sc2.filtered) == bool(sc.filtered)):
        return None
    dbm.set_score(conn, pid, sc2)
    return sc2


def _note_deadline(conn, pid, text) -> tuple[str, str] | None:
    """Record the deadline in this text, if it states one. Never raises."""
    try:
        got = deadlines.extract(text or "")
    except Exception:                      # a parser must never kill a scan
        return None
    if not got:
        return None
    try:
        dbm.set_deadline(conn, pid, got[0], got[1])
    except Exception:                                        # noqa: BLE001
        return None
    return got


def _fetch_source(cfg, mod, name: str, *, index: int, of: int, fetcher,
                  emit) -> dict:
    """Fetch one source. Network only: no database, no score, no budget.

    Sources are separate hosts, so these run in parallel; storage stays on the
    thread that owns the connection, because SQLite here gives us one.
    """
    emit({"kind": "source_start", "name": name, "index": index, "of": of})
    before = fetcher.requests
    try:
        postings = mod.fetch(source_settings(cfg, name), fetcher)
    except FetchError as e:
        fetcher.errors.append(f"{name}: {e.message}")
        postings = []
    except Exception as e:  # a broken source must never kill the run
        fetcher.errors.append(f"{name}: {type(e).__name__}: {e}")
        postings = []
    failed = not postings and any(e.startswith(f"{name}:") for e in fetcher.errors)
    return {"name": name, "postings": postings, "index": index, "of": of,
            "requests": fetcher.requests - before, "ok": not failed}


def _store_source(cfg, conn, mod, item: dict, *, run_id, min_score: float,
                  strong_th: float, probe_budget: dict, deadline_budget: dict,
                  fetcher, emit) -> dict:
    """Store what one fetch returned: upsert, score, probe, prune."""
    name = item["name"]
    postings = item["postings"]
    new_count = match_count = 0
    found: list[tuple] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for p in postings:
        if not p.is_valid():
            continue
        salmod.fill(p)
        sc = score_posting(cfg.prefs, p)
        pid, is_new, dup = dbm.upsert(conn, p, sc, run_id, now, cfg.prefs)
        # A deadline is worth recording whenever the text has one, and free
        # to look for: no network, just the teaser we already have.
        if p.description:
            _note_deadline(conn, pid, p.description)
        if is_new:
            new_count += 1
            if not sc.filtered and dup is None and sc.score >= min_score:
                match_count += 1
                found.append((pid, sc.score, p.title, p.company, p.location,
                              contract_label_from_reasons(sc.reasons)))
        # A source whose listing carries no body (jobs_ch, smartrecruiters,
        # jobup and others) would otherwise be scored on its title alone, while a
        # source that ships the text is scored on the advert. Fill the body first
        # for those, inside the same per-scan budget, so every connector is judged
        # on the same evidence.
        title_hit = any(str(lab).startswith("title:") for lab, _ in sc.reasons)
        if (not (p.description or "").strip() and title_hit
                and probe_budget.get("left", 0) > 0):
            fetch_detail = getattr(mod, "fetch_detail", None)
            text = ""
            if fetch_detail is not None:
                try:
                    text = fetch_detail(p, fetcher) or ""
                except Exception:                             # noqa: BLE001
                    text = ""
            if text.strip():
                body = clean_html_lines(text)
                conn.execute("UPDATE postings SET description=? WHERE id=?", (body, pid))
                sc = score_posting(cfg.prefs, replace(p, description=body))
                dbm.set_score(conn, pid, sc)
                probe_budget["left"] = probe_budget.get("left", 0) - 1
                if is_new:
                    match_count += 1 if (not sc.filtered and dup is None and sc.score >= min_score) else 0
                    if not sc.filtered and dup is None and sc.score >= min_score:
                        found.append((pid, sc.score, p.title, p.company, p.location,
                                      contract_label_from_reasons(sc.reasons)))

        probed = _maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher,
                                     probe_budget, dup, deadline_budget)
        if probed is not None:
            if probed.score >= strong_th:
                emit({"kind": "promoted", "id": pid, "score": probed.score,
                      "title": p.title})
            if is_new:
                for i, m in enumerate(found):
                    if m[0] == pid:
                        found[i] = (pid, probed.score, p.title, p.company,
                                    p.location,
                                    contract_label_from_reasons(probed.reasons))
                        break

    source_stats = {
        "fetched": len(postings),
        "new": new_count,
        "matches": match_count,
        "requests": item["requests"],
    }
    # A source may declare its own post-scan housekeeping (e.g. himalayas
    # pruning rows that can never be liveness-verified). Optional hook: a
    # module without `prune_unverifiable` is simply skipped.
    prune = getattr(mod, "prune_unverifiable", None)
    if prune is not None:
        try:
            removed = prune(conn, {**source_settings(cfg, name),
                                   "prune_below_score": min_score})
        except Exception as e:  # housekeeping must never kill the run
            fetcher.errors.append(
                f"{name}: prune failed: {type(e).__name__}: {e}")
            removed = 0
        if removed:
            source_stats["pruned"] = removed
            emit({"kind": "pruned", "name": name, "removed": removed})
    emit({"kind": "source_done", "name": name, "index": item["index"],
          "of": item["of"], "fetched": len(postings), "new": new_count,
          "matches": match_count})
    return {"name": name, "stats": source_stats, "new": new_count, "matches": found}


def scan(cfg, conn, fetcher, *,
         only: set[str] | None = None,
         on_event: OnEvent | None = None,
         cancel: Cancel | None = None,
         post_sources: Callable[[], dict | None] | None = None,
         force: bool = False) -> dict:
    """Run one scan. Returns the summary dict the CLI prints and the app shows.

    `post_sources` runs after the source loop but BEFORE the run is closed, so
    the CLI can slot its liveness verification in without this module needing to
    know what verification is.
    """
    emit: OnEvent = on_event or (lambda e: None)
    def cancelled() -> bool:
        return bool(cancel and cancel())

    only = only or set()
    min_score = float(cfg.prefs.get("min_score", 6))
    strong_th = float(cfg.prefs.get("strong_threshold", 12))
    probe_budget = {"left": int(cfg.prefs.get("detail_probe_per_scan", 0) or 0)}
    # Its own budget: a deadline is a different reason to fetch a page than the
    # contract type is, and one pass must not eat the other's allowance.
    deadline_budget = {"left": int(cfg.prefs.get("deadline_probe_per_scan", 25) or 0)}
    probe_start = probe_budget["left"]
    deadline_start = deadline_budget["left"]
    new_hash = prefs_hash(cfg.prefs)

    prev_run = dbm.get_run(conn)
    run_id = dbm.start_run(conn)

    result = dbm.maybe_rescore(conn, cfg.prefs, prev_run)
    if result and prev_run is not None:
        total, changed = result
        m, s = dbm.count_matches(conn, min_score, strong_th)
        emit({"kind": "rescored", "run_id": run_id, "previous_run": prev_run["id"],
              "total": total, "changed": changed, "matches": m, "strong": s})

    # Build the adapter map once: re-calling iter_sources per iteration would
    # re-resolve every source name through the registry on each loop pass.
    adapters = dict(iter_sources(cfg.enabled))
    names = [n for n in adapters if not only or n in only]

    # The setting exists to stop a scan repeating work it just did, but only the
    # boot scan honoured it: pressing Scan fetched all 47 sources again, 40
    # minutes after the last one. A source counts as fresh when it was fetched
    # successfully within the window; `force` overrides that.
    fresh_hours = float(cfg.prefs.get("scan_if_older_hours", 0) or 0)
    skipped_fresh: list[str] = []
    if fresh_hours > 0 and not force and names:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=fresh_hours)
        stamps = dbm.sources_scanned(conn)
        due: list[str] = []
        for n in names:
            stamp = stamps.get(n)
            fresh = False
            if stamp:
                try:
                    fresh = datetime.fromisoformat(stamp) > cutoff
                except ValueError:
                    fresh = False
            if fresh:
                skipped_fresh.append(n)
            else:
                due.append(n)
        if skipped_fresh:
            emit({"kind": "skipped_fresh", "names": skipped_fresh, "hours": fresh_hours})
        names = due

    # `--only X` where X exists but is NOT enabled scans nothing and looks
    # exactly like a source that had no jobs today. Name the skipped ones and
    # say what enables them.
    if only:
        skipped = sorted(n for n in only if n not in adapters)
        if skipped:
            emit({"kind": "not_enabled", "names": skipped,
                  "enabled_count": len(adapters)})
    stats = {"sources": {}, "requests": 0, "errors": [], "prefs_hash": new_hash}
    matches: list[tuple] = []
    total_new = 0

    emit({"kind": "start", "run_id": run_id, "sources": names,
          "count": len(names), "min_score": min_score, "strong_threshold": strong_th})

    stopped_early = False
    workers = max(1, int(cfg.prefs.get("scan_workers", 1) or 1))
    workers = min(workers, max(1, len(names)))
    emit_lock = threading.Lock()

    def safe_emit(e: Event) -> None:
        # Progress callbacks are not thread-safe (the CLI redraws a line in
        # place), and two sources finishing together would tear the output.
        with emit_lock:
            emit(e)

    items: list[dict] = []
    if workers == 1:
        for index, name in enumerate(names):
            if cancelled():
                stopped_early = True
                safe_emit({"kind": "cancelled", "after": index, "of": len(names)})
                break
            items.append(_fetch_source(cfg, adapters[name], name, index=index,
                                       of=len(names), fetcher=fetcher,
                                       emit=safe_emit))
            # Progress is a COUNT of finished sources, not their position in
            # the list: the window draws a bar from it, and a position can go
            # backwards.
            safe_emit({"kind": "source_fetched", "name": name,
                       "done": index + 1, "of": len(names)})
    else:
        # Sources are separate hosts, so fetching them together is still polite:
        # the fetcher keeps its 0.6s wait per host. Storage stays serial, because
        # SQLite hands us one connection and storage is cheap next to a fetch.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_source, cfg, adapters[name], name,
                            index=i, of=len(names), fetcher=fetcher,
                            emit=safe_emit): name
                for i, name in enumerate(names)
            }
            for done, fut in enumerate(as_completed(futures), 1):
                name = futures[fut]
                try:
                    items.append(fut.result())
                except Exception as e:  # one source must never kill the run
                    fetcher.errors.append(
                        f"{name}: scan crashed: {type(e).__name__}: {e}")
                # Counted on the thread that collects results, so `done` is
                # 1..N in completion order. Using the source's index here is
                # what made the bar jump back and forth: board 6 can answer
                # before board 2, and 6/40 followed by 3/40 is a rewind.
                safe_emit({"kind": "source_fetched", "name": name,
                           "done": done, "of": len(names)})

    for item in items:
        if cancelled():
            stopped_early = True
            break
        r = _store_source(cfg, conn, adapters[item["name"]], item,
                          run_id=run_id, min_score=min_score, strong_th=strong_th,
                          probe_budget=probe_budget,
                          deadline_budget=deadline_budget, fetcher=fetcher,
                          emit=safe_emit)
        stats["sources"][r["name"]] = r["stats"]
        total_new += r["new"]
        matches.extend(r["matches"])
        if item.get("ok"):
            dbm.mark_source_scanned(
                conn, r["name"], datetime.now(timezone.utc).isoformat(timespec="seconds"))

    # One health line per board that was asked, including the ones that answered
    # nothing: "zero, five runs in a row" is the only visible sign of a parser
    # that broke while the site kept saying 200.
    for name in names:
        st = stats["sources"].get(name) or {}
        errs = [str(e) for e in fetcher.errors if str(e).startswith(f"{name}:")]
        dbm.note_source_health(
            conn, name, int(st.get("fetched", 0) or 0),
            int(st.get("new", 0) or 0), "; ".join(errs)[:300])

    used_probes = probe_start - probe_budget["left"]
    if used_probes:
        stats["detail_probes"] = used_probes
    used_deadlines = deadline_start - deadline_budget["left"]
    if used_deadlines:
        stats["deadline_probes"] = used_deadlines
    extra = post_sources() if post_sources is not None else None
    stats["requests"] = fetcher.requests
    stats["errors"] = list(fetcher.errors)
    if extra:
        stats.update(extra)
    if stopped_early:
        stats["cancelled"] = True
    # Boards republish the same role under a second profile or with a title cut
    # at 100 characters, and a fresh posting id means the fingerprint cannot see
    # it. One pass over the table catches those pairs; doing it per upsert would
    # repeat the same comparison for every row of a 45,000-row store.
    deduped = dbm.dedupe_same_source(conn)
    # Two boards can carry one post word for word (jobs.ac.uk and THE both ran
    # the Cambridge museum directorship). Same-source passes cannot see that;
    # this one can, and prefers the employer's own board when either is direct.
    deduped += dbm.dedupe_cross_source(conn, set(DIRECT_SOURCES))
    if deduped:
        stats["deduped"] = deduped
    dbm.finish_run(conn, run_id, stats)
    m_all, s_all = dbm.count_matches(conn, min_score, strong_th)

    matches.sort(key=lambda x: -x[1])
    unmarked = conn.execute(
        "SELECT COUNT(*) n FROM postings WHERE status='new' AND filtered=0 "
        "AND dup_of IS NULL AND score >= ?", (min_score,)).fetchone()["n"]

    fetched_total = sum(int(s.get("fetched", 0) or 0) for s in stats["sources"].values())
    summary = {
        "run_id": run_id, "new": total_new, "fetched": fetched_total,
        "matches": len(matches),
        "total_matches": m_all, "strong": s_all, "min_score": min_score,
        "strong_threshold": strong_th, "matches_top": matches,
        "errors": list(fetcher.errors), "requests": fetcher.requests,
        "sources": stats["sources"], "cancelled": stopped_early,
        "unmarked": unmarked, "skipped_fresh": skipped_fresh,
        "deduped": int(stats.get("deduped", 0) or 0),
    }
    emit({"kind": "done", **{k: v for k, v in summary.items() if k != "matches_top"}})
    return summary
