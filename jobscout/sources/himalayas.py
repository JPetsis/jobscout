"""Himalayas — public jobs API (global remote roles, cursor paginated).

The API caps pages at 20 jobs, ignores server-side filters (category/title/
search params are silently dropped) and rate-limits hard: HTTP 429 after a few
dozen requests. So a scan walks only the newest `max_pages` pages and stops
early once postings older than `max_age_days` appear. Selection happens in the
scorer like every other source.

Salary comes structured (minSalary/maxSalary/currency/salaryPeriod) and is
folded into `compensation`, so the salary parser can read it.
"""
from __future__ import annotations

import datetime as _dt
import json
import urllib.parse

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, clean_html_lines, iso_from_epoch

NAME = "himalayas"
LABEL = "Himalayas"
API = "https://himalayas.app/jobs/api?limit=20"

_PERIOD = {"hourly": "hour", "daily": "day", "monthly": "month",
           "annual": "year", "yearly": "year"}


def _compensation(j: dict) -> str:
    lo, hi = j.get("minSalary"), j.get("maxSalary")
    if not lo and not hi:
        return ""
    if lo and hi and lo != hi:
        amount = f"{lo:g}-{hi:g}"
    else:
        amount = f"{lo or hi:g}"
    parts = [amount, (j.get("currency") or "").strip()]
    period = _PERIOD.get((j.get("salaryPeriod") or "").strip().lower(), "")
    if period:
        parts.append(f"per {period}")
    return " ".join(p for p in parts if p)


def parse(data) -> list[Posting]:
    jobs = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(jobs, list):
        return []
    out = []
    for j in jobs:
        title = (j.get("title") or "").strip()
        url = (j.get("guid") or j.get("applicationLink") or "").strip()
        if not title or not url:
            continue
        locs = j.get("locationRestrictions") or []
        out.append(Posting(
            source=NAME,
            title=title,
            company=(j.get("companyName") or "").strip(),
            location=", ".join(str(x) for x in locs),
            url=url,
            external_id=url,
            posted_at=iso_from_epoch(j.get("pubDate")),
            # The feed states its own expiry. Storing it is the ONLY way to
            # judge liveness for this source: every HTML posting page sits
            # behind a Cloudflare challenge, so the URL can never be probed,
            # but a date the publisher declared does not need probing.
            expires_at=iso_from_epoch(j.get("expiryDate")),
            description=clean_html_lines(j.get("description") or "")[:6000],
            compensation=_compensation(j),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    max_pages = int(settings.get("max_pages", 20))
    max_age_days = int(settings.get("max_age_days", 21))
    # Every HTML posting page on this source sits behind a Cloudflare
    # Turnstile challenge, so a stored row can never be liveness-verified by
    # URL. The feed's own `expiryDate` is the only authoritative signal, and it
    # is available at scan time. `require_expiry: true` therefore stores only
    # rows we will later be able to judge, instead of accumulating a tail of
    # permanently-unknown postings.
    require_expiry = bool(settings.get("require_expiry", False))
    skipped = 0
    cutoff = (_dt.date.today() - _dt.timedelta(days=max_age_days)).isoformat()
    out: list[Posting] = []
    cursor = None
    for page in range(max_pages):
        url = API + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        status, text = fetcher.get(url)
        if status != 200:
            fetcher.errors.append(f"himalayas: HTTP {status} after {page} page(s)")
            break
        try:
            data = json.loads(text)
        except ValueError as e:
            fetcher.errors.append(f"himalayas: invalid JSON: {e}")
            break
        batch = parse(data)
        if not batch:
            break
        if require_expiry:
            kept = [p for p in batch if p.expires_at]
            skipped += len(batch) - len(kept)
            batch = kept
        out.extend(batch)
        if not batch or min(p.posted_at or "9999" for p in batch) < cutoff:
            break                       # reached postings outside the age window
        cursor = data.get("nextCursor")
        if not cursor:
            break
    if skipped:
        fetcher.errors.append(f"himalayas: {skipped} posting(s) skipped, no publisher expiry")
    return out


def prune_unverifiable(conn, settings: dict) -> int:
    """Delete stored himalayas rows that can never be liveness-verified.

    Scoped deliberately NARROW, because the alternative is throwing away real
    matches:

      * only rows this source produced (other sources' rows are untouched)
      * only rows with no publisher expiry, i.e. provably unverifiable
      * only rows the user never acted on — a `saved`, `applied` or `dismissed`
        row is a decision, and no setting gets to delete a decision
      * only rows that could never surface: score below the minimum the user
        configured, or already filtered out

    Anything still in the live pool is left alone even if it has no expiry,
    because "we cannot verify it" is not a reason to remove a good lead. The
    honest fix for those is a new scan that re-adds them WITH an expiry.
    """
    if not settings.get("prune_unverifiable", False):
        return 0
    min_score = float(settings.get("prune_below_score", 0) or 0)
    cur = conn.execute(
        """DELETE FROM postings
           WHERE source = 'himalayas'
             AND expires_at = ''
             AND status = 'new'
             AND dup_of IS NULL
             AND (score < ? OR filtered = 1)""",
        (min_score,),
    )
    conn.commit()
    return cur.rowcount
