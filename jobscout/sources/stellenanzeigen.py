"""stellenanzeigen.de job search (server-rendered Next.js pages)."""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html

NAME = "stellenanzeigen"
LABEL = "stellenanzeigen.de"
BASE = "https://www.stellenanzeigen.de"

_RE_TITLE = re.compile(r'data-testid="enhanced-link"[^>]*title="([^"]{3,250})"')
_RE_TITLE_FALLBACK = re.compile(r"<h3[^>]*>\s*<a[^>]*>([^<]+)</a>", re.S)
_RE_COMPANY = re.compile(r'data-testid="company-name"[^>]*>([^<]+)<')
_RE_COMPANY_ALT = re.compile(r'data-testid="company-logo"[^>]*alt="([^"]+)')
_RE_LOC = re.compile(r"fa-map-marker-alt[^>]*></i><div[^>]*><span[^>]*>([^<]+)</span>")
_RE_HREF = re.compile(r'href="(/job/[^"]+)"')


def parse(text: str) -> list[Posting]:
    out, seen = [], set()
    for chunk in text.split('data-jobid="')[1:]:
        m_id = re.match(r"(SDE-\d+)", chunk.strip())
        href = _RE_HREF.search(chunk)
        title = _RE_TITLE.search(chunk) or _RE_TITLE_FALLBACK.search(chunk)
        if not m_id or not href or not title:
            continue
        if m_id.group(1) in seen:
            continue
        seen.add(m_id.group(1))
        company = _RE_COMPANY.search(chunk) or _RE_COMPANY_ALT.search(chunk)
        loc = _RE_LOC.search(chunk)
        out.append(Posting(
            source=NAME,
            title=clean_html(title.group(1)),
            company=clean_html(company.group(1)) if company else "",
            location=clean_html(loc.group(1)) if loc else "",
            url=BASE + href.group(1),
            external_id=m_id.group(1),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        kw = quote_plus(str(q.get("keywords", "")))
        loc = quote_plus(str(q.get("location", "")))
        for page in range(1, int(q.get("pages", 1)) + 1):
            url = f"{BASE}/suche/?fulltext={kw}&location={loc}&page={page}"
            status, text = fetcher.get(url, host_delay=0.8)
            if status != 200:
                fetcher.errors.append(f"stellenanzeigen: HTTP {status} for '{q.get('keywords')}' page {page}")
                continue
            out.extend(parse(text))
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
