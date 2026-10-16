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
from . import salary as salmod
from .config import source_settings
from .http import FetchError
from .score import prefs_hash, score_posting
from .sources import iter_sources

Event = dict
OnEvent = Callable[[Event], None]
Cancel = Callable[[], bool]


def _maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher, budget: dict,
                        dup) -> float | None:
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
    Returns the new score when the rescore changed it.
    """
    if budget.get("left", 0) <= 0 or sc.filtered or dup is not None:
        return None
    if sc.score < float(cfg.prefs.get("min_score", 6) or 0):
        return None
    if any(str(label).startswith("perm:") for label, _ in sc.reasons):
        return None                     # permanence already visible
    fn = getattr(mod, "fetch_detail", None)
    if fn is None:
        return None
    row = conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()
    if row is None or row["detail_fetched_at"]:
        return None
    budget["left"] -= 1
    try:
        desc = fn(dbm.row_to_posting(row), fetcher)
    except Exception as e:              # a probe must never kill the run
        fetcher.errors.append(f"{p.source}: detail probe failed: "
                              f"{type(e).__name__}: {e}")
        return None
    if not desc:
        return None
    dbm.set_description(conn, pid, desc)
    row = conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()
    sc2 = score_posting(cfg.prefs, dbm.row_to_posting(row))
    if sc2.score == sc.score and bool(sc2.filtered) == bool(sc.filtered):
        return None
    dbm.set_score(conn, pid, sc2)
    return sc2.score


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
    probe_start = probe_budget["left"]
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
            if is_new:
                new_count += 1
                if not sc.filtered and dup is None and sc.score >= min_score:
                    match_count += 1
                    matches.append((pid, sc.score, p.title, p.company, p.location))
            probed = _maybe_probe_detail(conn, cfg, mod, p, pid, sc, fetcher,
                                         probe_budget, dup)
            if probed is not None:
                if probed >= strong_th:
                    emit({"kind": "promoted", "id": pid, "score": probed,
                          "title": p.title})
                if is_new:
                    for i, m in enumerate(matches):
                        if m[0] == pid:
                            matches[i] = (pid, probed, p.title, p.company, p.location)
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
