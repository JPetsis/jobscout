"""LinkedIn public guest search — no login, no API key.

Uses the same endpoints the public jobs pages call. Keep request volume low:
LinkedIn answers 429 quickly when hammered, so the source enforces a hard
per-run budget (max_requests) and paces requests.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, date_part

NAME = "linkedin"
LABEL = "LinkedIn"
SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

_CARD_SPLIT = 'data-entity-urn="urn:li:jobPosting:'
_RE_ID = re.compile(r"^(\d+)")
_RE_URL = re.compile(r'href="(https://[a-z.]*linkedin\.com/jobs/view/[^"?]+)')
_RE_TITLE = re.compile(r'class="base-search-card__title"[^>]*>\s*(.*?)\s*</h3>', re.S)
_RE_COMPANY = re.compile(r'class="base-search-card__subtitle"[^>]*>\s*<a[^>]*>\s*(.*?)\s*</a>', re.S)
_RE_LOC = re.compile(r'class="job-search-card__location"[^>]*>\s*(.*?)\s*</span>', re.S)
_RE_TIME = re.compile(r'<time[^>]*datetime="([\d-]+)"')
_RE_DETAIL = re.compile(r'show-more-less-html__markup[^>]*>')


def _slice_div(text: str, open_end: int) -> str:
    """Return the content of a <div> whose opening tag ends at ``open_end``."""
    depth = 1
    for m in re.finditer(r"<(/?)div[^>]*>", text[open_end:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return text[open_end : open_end + m.start()]
    return text[open_end : open_end + 40000]


def parse(text: str) -> list[Posting]:
    out = []
    for chunk in text.split(_CARD_SPLIT)[1:]:
        m_id = _RE_ID.match(chunk)
        if not m_id:
            continue
        url_m = _RE_URL.search(chunk)
        title_m = _RE_TITLE.search(chunk)
        if not title_m:
            continue
        company_m = _RE_COMPANY.search(chunk)
        loc_m = _RE_LOC.search(chunk)
        date_m = _RE_TIME.search(chunk)
        out.append(Posting(
            source=NAME,
            title=clean_html(title_m.group(1)),
            company=clean_html(company_m.group(1)) if company_m else "",
            location=clean_html(loc_m.group(1)) if loc_m else "",
            url=url_m.group(1) if url_m else "",
            external_id=m_id.group(1),
            posted_at=date_part(date_m.group(1)) if date_m else "",
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    budget = int(settings.get("max_requests", 24))
    fresh_days = int(settings.get("fresh_days", 0) or 0)
    f_tpr = f"&f_TPR=r{fresh_days * 86400}" if fresh_days else ""
    out: list[Posting] = []
    used = 0
    stop = False
    for q in settings.get("queries", []):
        if stop:
            break
        kw = quote_plus(str(q.get("keywords", "")))
        loc = quote_plus(str(q.get("location", "")))
        for page in range(int(q.get("pages", 1))):
            if used >= budget:
                fetcher.errors.append(f"linkedin: request budget reached ({budget})")
                stop = True
                break
            url = (f"{SEARCH}?keywords={kw}&location={loc}"
                   f"&start={page * 10}{f_tpr}")
            status, text = fetcher.get(url, headers={"Accept": "text/html"}, host_delay=1.5)
            used += 1
            if status == 429:
                fetcher.errors.append("linkedin: rate limited (HTTP 429) — stopping this source")
                stop = True
                break
            if status != 200:
                fetcher.errors.append(f"linkedin: HTTP {status}")
                continue
            out.extend(parse(text))
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    if not p.external_id:
        return None
    status, text = fetcher.get(DETAIL.format(p.external_id), headers={"Accept": "text/html"})
    if status != 200:
        return None
    m = _RE_DETAIL.search(text)
    if not m:
        return None
    return clean_html(_slice_div(text, m.end()))
