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

from datetime import datetime, timezone
from typing import Callable

from . import db as dbm
from . import deadlines
from . import salary as salmod
from .config import source_settings
from .http import FetchError
from .score import contract_label_from_reasons, prefs_hash, score_posting
from .sources import iter_sources

Event = dict
OnEvent = Callable[[Event], None]
Cancel = Callable[[], bool]


def _maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher, budget: dict,
                        dup, deadline_budget: dict | None = None) -> float | None:
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
    try:
        desc = fn(dbm.row_to_posting(row), fetcher)
    except Exception as e:              # a probe must never kill the run
        fetcher.errors.append(f"{p.source}: detail probe failed: "
                              f"{type(e).__name__}: {e}")
        return None
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


def scan(cfg, conn, fetcher, *,
         only: set[str] | None = None,
         on_event: OnEvent | None = None,
         cancel: Cancel | None = None,
         post_sources: Callable[[], dict | None] | None = None) -> dict:
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
    for index, name in enumerate(names):
        if cancelled():
            stopped_early = True
            emit({"kind": "cancelled", "after": index, "of": len(names)})
            break
        mod = adapters[name]
        emit({"kind": "source_start", "name": name, "index": index, "of": len(names)})
        settings = source_settings(cfg, name)
        before = fetcher.requests
        try:
            postings = mod.fetch(settings, fetcher)
        except FetchError as e:
            fetcher.errors.append(f"{name}: {e.message}")
            postings = []
        except Exception as e:  # a broken source must never kill the run
            fetcher.errors.append(f"{name}: {type(e).__name__}: {e}")
            postings = []
        new_count = match_count = 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for p in postings:
            if not p.is_valid():
                continue
            salmod.fill(p)
            sc = score_posting(cfg.prefs, p)
            pid, is_new, dup = dbm.upsert(conn, p, sc, run_id, now, cfg.prefs)
            # A deadline is worth recording whenever the text has one, and free
            # to look for: no network, just the teaser we already have. Most
            # boards publish no deadline at all (0.25% of stored teasers on this
            # store), which is exactly why the probe below exists.
            if p.description:
                _note_deadline(conn, pid, p.description)
            if is_new:
                new_count += 1
                if not sc.filtered and dup is None and sc.score >= min_score:
                    match_count += 1
                    matches.append((pid, sc.score, p.title, p.company, p.location,
                                    contract_label_from_reasons(sc.reasons)))
            probed = _maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher,
                                         probe_budget, dup, deadline_budget)
            if probed is not None:
                if probed.score >= strong_th:
                    emit({"kind": "promoted", "id": pid, "score": probed.score,
                          "title": p.title})
                if is_new:
                    for i, m in enumerate(matches):
                        if m[0] == pid:
                            matches[i] = (pid, probed.score, p.title, p.company,
                                          p.location,
                                          contract_label_from_reasons(probed.reasons))
                            break
        stats["sources"][name] = {
            "fetched": len(postings),
            "new": new_count,
            "matches": match_count,
            "requests": fetcher.requests - before,
        }
        # A source may declare its own post-scan housekeeping (e.g. himalayas
        # pruning rows that can never be liveness-verified). Optional hook: a
        # module without `prune_unverifiable` is simply skipped.
        prune = getattr(mod, "prune_unverifiable", None)
        if prune is not None:
            try:
                removed = prune(conn, {**settings, "prune_below_score": min_score})
            except Exception as e:  # housekeeping must never kill the run
                fetcher.errors.append(f"{name}: prune failed: {type(e).__name__}: {e}")
                removed = 0
            if removed:
                stats["sources"][name]["pruned"] = removed
                emit({"kind": "pruned", "name": name, "removed": removed})
        total_new += new_count
        emit({"kind": "source_done", "name": name, "index": index, "of": len(names),
              "fetched": len(postings), "new": new_count, "matches": match_count})

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
    dbm.finish_run(conn, run_id, stats)
    m_all, s_all = dbm.count_matches(conn, min_score, strong_th)

    matches.sort(key=lambda x: -x[1])
    unmarked = conn.execute(
        "SELECT COUNT(*) n FROM postings WHERE status='new' AND filtered=0 "
        "AND dup_of IS NULL AND score >= ?", (min_score,)).fetchone()["n"]

    summary = {
        "run_id": run_id, "new": total_new, "matches": len(matches),
        "total_matches": m_all, "strong": s_all, "min_score": min_score,
        "strong_threshold": strong_th, "matches_top": matches,
        "errors": list(fetcher.errors), "requests": fetcher.requests,
        "sources": stats["sources"], "cancelled": stopped_early,
        "unmarked": unmarked,
    }
    emit({"kind": "done", **{k: v for k, v in summary.items() if k != "matches_top"}})
    return summary
