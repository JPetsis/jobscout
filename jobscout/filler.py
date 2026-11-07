"""Fill missing advert bodies from the store, not from a board's listing.

Half the connectors return a title and a link, with the advert behind a detail
page. Scoring happens during the scan, so those postings were judged on their
title while a board that ships the text was judged on the advert itself.

The work is a query over the store, which is the point: a posting that fell off
the board's listing is still here and still gets its turn. Each host has its own
pace, a refusal halves it, and a second refusal parks the host for the day. A row
is fetched once ever, stamped, and rescored with what was read.
"""
from __future__ import annotations

import threading
import time
import urllib.parse
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from . import db as dbm
from . import detail as detailmod
from . import sources
from .score import score_posting
from .textutil import clean_html_lines

BATCH = 2000
SECONDS = 600
HOST_INTERVAL = 1.0
MAX_HOSTS = 3
#: A description shorter than this is a teaser or nothing, not an advert.
MIN_BODY = 200


def queue(conn, limit: int = BATCH) -> list[dict]:
    """Rows that still need a body, best-scoring first.

    Dead rows are skipped: there is no point reading an advert the board no
    longer lists. Everything else with no body and no earlier attempt is fair
    game, whatever the board and however old.
    """
    rows = conn.execute(
        "SELECT id, source, url, title, company, location, external_id, score "
        "FROM postings WHERE length(description) < ? AND detail_fetched_at IS NULL "
        "AND dup_of IS NULL AND verify_status != 'dead' "
        "ORDER BY score DESC, id DESC LIMIT ?", (MIN_BODY, int(limit))).fetchall()
    return [dict(r) for r in rows]


class Pacer:
    """One pace per host, with a memory of refusals."""

    def __init__(self, interval: float = HOST_INTERVAL, clock=time.monotonic):
        self.base = float(interval)
        self.clock = clock
        self.next_at: dict[str, float] = {}
        self.interval: dict[str, float] = {}
        self.parked: set[str] = set()
        self.strikes: dict[str, int] = {}
        self.lock = threading.Lock()

    def wait(self, host: str) -> bool:
        """Sleep until the host may be asked again. False if it is parked."""
        with self.lock:
            if host in self.parked:
                return False
            due = self.next_at.get(host, 0.0)
            gap = self.interval.get(host, self.base)
        delay = due - self.clock()
        if delay > 0:
            time.sleep(delay)
        with self.lock:
            self.next_at[host] = self.clock() + gap
        return True

    def refused(self, host: str) -> None:
        """A 429 halves the pace; a second one parks the host for the day."""
        with self.lock:
            self.strikes[host] = self.strikes.get(host, 0) + 1
            if self.strikes[host] >= 2:
                self.parked.add(host)
            else:
                self.interval[host] = self.interval.get(host, self.base) * 2


def _text_for(row: dict, fetcher) -> str:
    mod = sources.REGISTRY.get(row["source"])
    if mod is None:
        return ""
    fetch_detail = getattr(mod, "fetch_detail", None)
    if fetch_detail is not None:
        try:
            return fetch_detail(_posting(row), fetcher) or ""
        except Exception:                                     # noqa: BLE001
            return ""
    try:
        return detailmod.html_detail(row["url"], fetcher) or ""
    except Exception:                                         # noqa: BLE001
        return ""


def _posting(row: dict):
    from .models import Posting
    return Posting(source=row["source"], title=row["title"] or "",
                   company=row["company"] or "", location=row["location"] or "",
                   url=row["url"] or "", external_id=row["external_id"] or "")


def _title_hit(prefs: dict, row: dict) -> bool:
    """Would this posting match on its title alone?

    The same gate the scan uses: a body is worth reading only for an advert the
    profile could show, and a title with no hit is not a match by the scoring
    rules anyway.
    """
    try:
        sc = score_posting(prefs, _posting(row))
    except Exception:                                         # noqa: BLE001
        return False
    return any(str(lab).startswith("title:") for lab, _ in sc.reasons)


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url or "").netloc


def run(cfg, conn, fetcher, *, limit: int = BATCH, seconds: float = SECONDS,
        progress=None, cancel=None, pacer: Pacer | None = None) -> dict:
    """Work the queue until it is empty, the budget is spent or time is up."""
    # Only postings this profile could match: filling bodies for the other
    # fourteen thousand would spend hours on adverts that can never appear.
    rows = [r for r in queue(conn, 100000) if _title_hit(cfg.prefs, r)][:int(limit)]
    total = len(rows)
    done = filled = 0
    parked: set[str] = set()
    if not rows:
        return {"total": 0, "done": 0, "filled": 0, "parked": []}

    by_host: dict[str, list[dict]] = {}
    for row in rows:
        by_host.setdefault(_host(row["url"]), []).append(row)

    pacer = pacer or Pacer()
    deadline = time.monotonic() + float(seconds)

    def work(items: list[dict]) -> None:
        nonlocal done, filled
        for row in items:
            if cancel is not None and cancel():
                return
            if time.monotonic() > deadline:
                return
            host = _host(row["url"])
            if not pacer.wait(host):
                parked.add(host)
                return
            text = _text_for(row, fetcher)
            # One connection, several host threads: sqlite3 is not safe to use
            # concurrently, and the writes are milliseconds against a request
            # that takes a second, so serialising them costs nothing.
            with dbm.LOCK:
                if text.strip():
                    body = clean_html_lines(text)
                    conn.execute("UPDATE postings SET description=? WHERE id=?",
                                 (body, row["id"]))
                    p = _posting(row)
                    p.description = body
                    sc = score_posting(cfg.prefs, p)
                    dbm.set_score(conn, row["id"], sc)
                    filled += 1
                # Once ever, filled or not: a page with no body is a fact about
                # that advert, and coming back to it on every run would spend
                # the budget on the same failures.
                conn.execute("UPDATE postings SET detail_fetched_at=? WHERE id=?",
                             (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              row["id"]))
                if done % 10 == 0:
                    conn.commit()
            done += 1
            if progress is not None:
                progress(done, total, row["source"])

    with ThreadPoolExecutor(max_workers=max(1, min(MAX_HOSTS, len(by_host)))) as pool:
        list(pool.map(work, by_host.values()))
    conn.commit()
    return {"total": total, "done": done, "filled": filled, "parked": sorted(parked)}
