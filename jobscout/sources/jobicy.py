"""Jobicy — free public remote-jobs API (no key).

`https://jobicy.com/api/v2/remote-jobs` returns `jobs[]` with structured
salary fields (min/max/currency/period), `pubDate`, `jobDescription`, and
`hasMore` + `nextCursor` cursor pagination. Verified live 2026-10-15: count=50
is accepted, and `geo`/`industry`/`tag` filters apply server-side (geo=germany
also returns "Anywhere" remote rows, so treat jobGeo as text, not a gate).

LICENCE CONDITION: Jobicy's own notice asks that Jobicy be clearly credited
with a direct link wherever its jobs are shown. The README credits it; keep
that credit if this adapter is ever reused elsewhere.
"""
from __future__ import annotations

from urllib.parse import quote, urlencode

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, clean_html_lines, date_part

NAME = "jobicy"
LABEL = "Jobicy"
API = "https://jobicy.com/api/v2/remote-jobs"

_PERIOD = {"hourly": "hour", "daily": "day", "monthly": "month",
           "yearly": "year", "annual": "year"}


def _compensation(j: dict) -> str:
    lo, hi = j.get("salaryMin"), j.get("salaryMax")
    if not lo and not hi:
        return ""
    try:
        lo_n = float(lo) if lo else None
        hi_n = float(hi) if hi else None
    except (TypeError, ValueError):
        return ""
    if lo_n and hi_n and lo_n != hi_n:
        amount = f"{lo_n:g}-{hi_n:g}"
    else:
        amount = f"{lo_n or hi_n:g}"
    parts = [amount, (j.get("salaryCurrency") or "").strip()]
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
        if not isinstance(j, dict):
            continue
        title = (j.get("jobTitle") or "").strip()
        url = (j.get("url") or "").strip()
        if not title or not url:
            continue
        out.append(Posting(
            source=NAME,
            title=title,
            company=(j.get("companyName") or "").strip(),
            location=(j.get("jobGeo") or "").strip(),
            url=url,
            external_id=str(j.get("id") or j.get("jobSlug") or url),
            posted_at=date_part(str(j.get("pubDate") or "")),
            description=(clean_html_lines(j.get("jobDescription") or "")
                         or clean_html(j.get("jobExcerpt") or ""))[:6000],
            compensation=_compensation(j),
            extra={"level": j.get("jobLevel"),
                   "industry": j.get("jobIndustry")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    count = max(1, min(int(settings.get("count", 50) or 50), 50))
    max_pages = max(1, int(settings.get("max_pages", 3) or 3))
    params: dict = {"count": count}
    for key in ("geo", "industry", "tag"):
        v = str(settings.get(key) or "").strip()
        if v:
            params[key] = v
    out: list[Posting] = []
    cursor = ""
    for _ in range(max_pages):
        url = f"{API}?{urlencode(params)}"
        if cursor:
            url += f"&cursor={quote(cursor)}"
        try:
            data = fetcher.get_json(url)
        except Exception as e:
            fetcher.errors.append(f"{NAME}: {e}")
            break
        out.extend(parse(data))
        if not isinstance(data, dict) or not data.get("hasMore"):
            break
        cursor = str(data.get("nextCursor") or "")
        if not cursor:
            break
    return out
