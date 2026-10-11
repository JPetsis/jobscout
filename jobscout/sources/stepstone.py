"""StepStone (DE) job search — server-rendered result list."""
from __future__ import annotations

import re

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, de_rel_date, slug

NAME = "stepstone"
LABEL = "StepStone"
BASE = "https://www.stepstone.de"

_CARD_SPLIT = 'data-testid="job-item"'
_RE_HREF = re.compile(r'href="(/stellenangebote--[^"]+)"')
_RE_TITLE = re.compile(r'data-at="job-item-title"[^>]*>(.*?)</a>', re.S)
_RE_COMPANY = re.compile(r'data-genesis-element="COMPANY_LOGO_IMAGE"[^>]*\balt="([^"]+)"')
_RE_COMPANY_FALLBACK = re.compile(r'\balt="([^"]{2,120})"')
_RE_LOC = re.compile(r'data-at="job-item-location"[^>]*>(.*?)</div>', re.S)
_RE_DATE = re.compile(r'data-at="job-item-timeago"[^>]*>.*?<time[^>]*>([^<]{2,60})</time>', re.S)
_RE_ID = re.compile(r"--(\d+)-inline\.html")


def parse(text: str) -> list[Posting]:
    out, seen = [], set()
    for chunk in text.split(_CARD_SPLIT)[1:]:
        href = _RE_HREF.search(chunk)
        title = _RE_TITLE.search(chunk)
        if not href or not title:
            continue
        url = BASE + href.group(1)
        if url in seen:
            continue
        seen.add(url)
        company = _RE_COMPANY.search(chunk) or _RE_COMPANY_FALLBACK.search(chunk)
        loc = _RE_LOC.search(chunk)
        date = _RE_DATE.search(chunk)
        m_id = _RE_ID.search(href.group(1))
        out.append(Posting(
            source=NAME,
            title=clean_html(title.group(1)),
            company=clean_html(company.group(1)) if company else "",
            location=clean_html(loc.group(1)) if loc else "",
            url=url,
            external_id=m_id.group(1) if m_id else "",
            posted_at=de_rel_date(date.group(1)) if date else "",
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        kw = slug(str(q.get("keywords", "")))
        loc = slug(str(q.get("location", "")))
        base = f"{BASE}/jobs/{kw}" + (f"/in-{loc}" if loc else "")
        for page in range(1, int(q.get("pages", 1)) + 1):
            url = f"{base}?page={page}"
            status, text = fetcher.get(url, host_delay=1.2)
            if status != 200:
                fetcher.errors.append(f"stepstone: HTTP {status} for '{q.get('keywords')}' page {page}")
                continue
            out.extend(parse(text))
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
