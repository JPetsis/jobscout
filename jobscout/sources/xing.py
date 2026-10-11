"""XING job search (public pages, server-rendered)."""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, date_part, de_rel_date, slug

NAME = "xing"
LABEL = "XING"
BASE = "https://www.xing.com"

_CARD_SPLIT = 'data-testid="job-search-result"'
_RE_LINK = re.compile(r'href="(/jobs/[^"]+)"')
_RE_TITLE = re.compile(r'data-testid="job-teaser-list-title"[^>]*>([^<]{3,250})<')
_RE_COMPANY = re.compile(r'job-teaser-list-item-styles__Company[^"]*"[^>]*>([^<]+)<')
_RE_LOC = re.compile(r'multi-location-display-styles__Container[^"]*"[^>]*>.*?<p[^>]*>([^<]+)', re.S)
_RE_DATE_ISO = re.compile(r'job-teaser-list-item-styles__PublicationDate[^"]*"[^>]*>.*?<time[^>]*dateTime="([^"]+)"', re.S)
_RE_DATE_TEXT = re.compile(r'job-teaser-list-item-styles__PublicationDate[^"]*"[^>]*>.*?<span[^>]*>([^<]{2,40})<', re.S)


def parse(text: str) -> list[Posting]:
    out = []
    for chunk in text.split(_CARD_SPLIT)[1:]:
        link = _RE_LINK.search(chunk)
        title = _RE_TITLE.search(chunk)
        if not title:
            continue
        company = _RE_COMPANY.search(chunk)
        loc = _RE_LOC.search(chunk)
        date_iso = _RE_DATE_ISO.search(chunk)
        date_txt = _RE_DATE_TEXT.search(chunk)
        posted = ""
        if date_iso:
            posted = date_part(date_iso.group(1))
        elif date_txt:
            posted = de_rel_date(date_txt.group(1))
        href = link.group(1) if link else ""
        out.append(Posting(
            source=NAME,
            title=clean_html(title.group(1)),
            company=clean_html(company.group(1)) if company else "",
            location=clean_html(loc.group(1)) if loc else "",
            url=(BASE + href) if href.startswith("/") else href,
            external_id=href.rsplit("-", 1)[-1] if href else "",
            posted_at=posted,
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        kw = quote_plus(str(q.get("keywords", "")))
        loc = quote_plus(str(q.get("location", "")))
        for page in range(1, int(q.get("pages", 1)) + 1):
            url = f"{BASE}/jobs/search?keywords={kw}&location={loc}&page={page}"
            status, text = fetcher.get(url, host_delay=1.0)
            if status != 200:
                fetcher.errors.append(f"xing: HTTP {status} for '{q.get('keywords')}' page {page}")
                continue
            out.extend(parse(text))
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
