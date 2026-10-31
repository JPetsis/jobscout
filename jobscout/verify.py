"""Is the posting still open? One pass over the rows the board can show.

Three verdicts come for free before a single request is spent:

* a scan that just returned the posting means it is open -- `db.upsert` records
  that, and revives a re-advertised row that had been marked dead;
* a publisher-stated expiry in the past is dead without a request, which
  `liveness.check` already decides;
* a board that publishes its WHOLE job list answers for every row from that
  board in one request, in both directions. Absence there is evidence, which it
  is not on a paged search: XING page 1 says nothing about page 5.

Everything else is one GET per row, in a worker pool. The pass runs over the
rows the board can actually show, not the whole store: 45,000 stored postings
take hours and 43,000 of them will never be opened.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import db as dbm
from . import liveness
from .http import Fetcher

#: Sources whose public API lists every live posting, keyed by the board token
#: found in a posting URL. Membership is a verdict; absence is a dead row.
_BOARDS = {
    "greenhouse": re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([^/?#]+)/jobs/(\d+)"),
    "lever": re.compile(r"jobs\.lever\.co/([^/?#]+)/([0-9a-fA-F-]{36})"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-fA-F-]{36})"),
    "smartrecruiters": re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)/(\d+)"),
}


#: Hosts that answered 429 at the shared 0.6s pace. Measured on a real pass:
#: 64 of 129 arbeitnow rows came back rate-limited while every other host was
#: fine, so one host gets a slower pace rather than the whole pass slowing down.
_SLOW_HOSTS = {"www.arbeitnow.com": 1.1}

#: After a 429, wait this long and try the row once more before giving up on
#: that host for the rest of the pass.
_RATE_LIMIT_PAUSE = 5.0


class _Paced:
    """A fetcher that paces one host slower than the rest.

    `Fetcher` already serialises per host and waits `host_delay` between calls;
    this only changes the delay for a named host, so a board with a tighter
    limit is probed politely without slowing the other forty.
    """

    def __init__(self, fetcher: Fetcher, delays: dict[str, float]):
        self.fetcher = fetcher
        self.delays = delays

    def get(self, url: str, headers: dict | None = None, **kw):
        host = urllib.parse.urlsplit(url).netloc
        if host in self.delays:
            kw.setdefault("host_delay", self.delays[host])
        return self.fetcher.get(url, headers=headers, **kw)


def _get_json(fetcher: Fetcher, url: str):
    try:
        status, text = fetcher.get(url)
    except Exception as e:                                      # noqa: BLE001
        return None, f"unreachable ({type(e).__name__})"
    if status != 200:
        return None, f"HTTP {status}"
    try:
        return json.loads(text), ""
    except ValueError:
        return None, "unexpected answer"


def _greenhouse(fetcher: Fetcher, board: str):
    data, note = _get_json(
        fetcher, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=false")
    if data is None:
        return None, note
    return {str(j.get("id")) for j in (data.get("jobs") or [])}, ""


def _lever(fetcher: Fetcher, board: str):
    data, note = _get_json(fetcher, f"https://api.lever.co/v0/postings/{board}?mode=json")
    if data is None:
        return None, note
    if not isinstance(data, list):
        return None, "unexpected answer"
    return {str(j.get("id")) for j in data if isinstance(j, dict)}, ""


def _ashby(fetcher: Fetcher, board: str):
    data, note = _get_json(
        fetcher, f"https://api.ashbyhq.com/posting-api/job-board/{board}")
    if data is None:
        return None, note
    jobs = (data.get("jobs") or []) if isinstance(data, dict) else []
    ids = {str(j.get("id")) for j in jobs if isinstance(j, dict)}
    urls = " ".join(str(j.get("jobUrl") or "") for j in jobs if isinstance(j, dict))
    return ids, urls


def _smartrecruiters(fetcher: Fetcher, board: str):
    """Up to ten pages of 100. Complete only when `totalFound` is covered.

    Absence is a dead verdict only from a complete list: stopping short while
    the board still has jobs would kill every row after the last page. Measured:
    BayWaAG has 898 postings, so three pages left the whole board unprovable and
    every row needed its own probe; nine settle it in nine requests.
    """
    ids: set[str] = set()
    offset = 0
    for _ in range(10):
        data, note = _get_json(
            fetcher,
            f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
            f"?limit=100&offset={offset}")
        if data is None:
            return None, note
        batch = data.get("content") or []
        ids.update(str(j.get("id")) for j in batch)
        offset += len(batch)
        total = int(data.get("totalFound") or 0)
        if not batch or offset >= total:
            return ids, ""          # complete
    return ids, "incomplete"        # more than 1000: confirm only, never kill


_LISTERS = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
    "smartrecruiters": _smartrecruiters,
}


def _board_of(url: str):
    for kind, pattern in _BOARDS.items():
        m = pattern.search(url or "")
        if m:
            return kind, m.group(1), m.group(2)
    return None


def _in_list(verdict, row, pid: str) -> bool:
    """Is this row's job in the board's list?

    The URL's own id is the key the list publishes, so it is preferred over
    `external_id`, which some sources fill with a slug or leave empty.
    """
    ids, urls = verdict if isinstance(verdict, tuple) else (verdict, "")
    key = pid or str(row["external_id"] or "")
    if key and key in ids:
        return True
    return bool(urls) and bool(key) and key.lower() in urls.lower()


def run_pass(cfg, conn, *, min_score: float | None = None, max_age_days: int = 3,
             limit: int | None = None, workers: int = 6,
             progress=None, cancel=None) -> dict:
    """Check the rows the board can show. Returns counts.

    `progress(done, total, counts)` is called after every verdict so a window
    can draw a bar; `cancel()` is polled between rows.
    """
    prefs = cfg.prefs
    if min_score is None:
        min_score = float(prefs.get("min_score", 6))
    rows = dbm.postings_to_check(conn, min_score, max_age_days=max_age_days,
                                 limit=limit)
    total = len(rows)
    counts = {"ok": 0, "dead": 0, "unknown": 0, "batched": 0}
    if not total:
        return {"total": 0, **counts, "cancelled": False}

    fetcher = Fetcher(host_delay=float(prefs.get("host_delay", 0.6) or 0))
    done = 0
    dead_titles: list[str] = []

    def record(row, state: str, note: str) -> None:
        nonlocal done
        dbm.update_verify(conn, row["id"], state, note)
        counts[state] += 1
        done += 1
        if state == "dead":
            dead_titles.append(f"{row['title'][:60]} ({row['source']})")
        if progress is not None:
            progress(done, total, counts)

    # --- one request per board, in both directions -------------------------
    groups: dict[tuple[str, str], list] = {}
    singles: list = []
    for row in rows:
        found = _board_of(row["url"])
        if found:
            kind, board, _pid = found
            groups.setdefault((kind, board), []).append(row)
        else:
            singles.append(row)

    for (kind, board), group in groups.items():
        if cancel is not None and cancel():
            break
        verdict, note = _LISTERS[kind](fetcher, board)
        if verdict is None:
            # The board list could not be read: fall back to per-row probes
            # rather than guessing. A broken list must never kill rows.
            singles.extend(group)
            continue
        complete = note != "incomplete"
        for row in group:
            found = _board_of(row["url"])
            pid = found[2] if found else ""
            if _in_list(verdict, row, pid):
                record(row, "ok", "")
            elif complete:
                record(row, "dead", f"no longer on the {kind} board list")
            else:
                record(row, "unknown", f"{kind} board list too long to prove absence")
        counts["batched"] += len(group)

    # --- one GET per row, in parallel ACROSS hosts, serial within one ------
    paced = _Paced(fetcher, _SLOW_HOSTS)
    by_host: dict[str, list] = {}
    for row in singles:
        host = urllib.parse.urlsplit(row["url"]).netloc
        by_host.setdefault(host, []).append(row)

    def probe_host(group: list) -> list:
        """Rows of ONE host, oldest first. A 429 ends the host for this pass.

        Six workers all reading the same board is what produced 64 rate-limited
        rows on a real pass; the parallelism belongs between hosts, not inside
        one.
        """
        out = []
        for i, row in enumerate(group):
            if cancel is not None and cancel():
                break
            state, note = liveness.check(paced, row["url"], row["expires_at"] or "")
            if state == "unknown" and "429" in note:
                time.sleep(_RATE_LIMIT_PAUSE)
                state, note = liveness.check(paced, row["url"], row["expires_at"] or "")
                if state == "unknown" and "429" in note:
                    out.append((row, state, note))
                    for rest in group[i + 1:]:
                        out.append((rest, "unknown", "host rate-limited; left for the next pass"))
                    break
            out.append((row, state, note))
        return out

    if by_host and not (cancel is not None and cancel()):
        with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 8))) as pool:
            for results in pool.map(probe_host, by_host.values()):
                for row, state, note in results:
                    record(row, state, note[:120])

    return {"total": total, **counts, "cancelled": bool(cancel and cancel()),
            "dead_titles": dead_titles[:20]}
