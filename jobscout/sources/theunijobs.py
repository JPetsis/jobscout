"""THE unijobs — Times Higher Education's academic board (UK-based, worldwide).

Server-rendered, no key, 20 per page with `page=N` and `keywords=`. It is the
board that carries the European professorial adverts along with the British
ones: an archaeology search returns the Munich W2 professorship next to a
Birkbeck lectureship, and the card states the city, the grade (W2, Grade 8) and
the employer, which is enough to score it before the advert is opened.

Cards carry no closing date — THE lists "10 days ago" instead — so `expires_at`
stays empty here and liveness is decided by the advert itself.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import quote_plus

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html

NAME = "theunijobs"
LABEL = "THE unijobs"
BASE = "https://www.timeshighereducation.com"
SEARCH = f"{BASE}/unijobs/listings/"
PAGE_SIZE = 20

_CARD_SPLIT = '<li class="lister__item'
_RE_ID = re.compile(r'id="item-(\d+)"')
# THE breaks the anchor across lines ("<a \n\n href=\"\n\t/unijobs/…"), so
# the tag and its href cannot be assumed adjacent.
_RE_HREF = re.compile(r'<a\s[^>]*href="\s*(/unijobs/listing/[^"?]+)')
_RE_TITLE = re.compile(r'lister__header.*?<span>(.*?)</span>', re.S)
_RE_LOC = re.compile(r'lister__meta-item--location">(.*?)</li>', re.S)
_RE_SALARY = re.compile(r'lister__meta-item--salary">(.*?)</li>', re.S)
_RE_EMPLOYER = re.compile(r'lister__meta-item--recruiter">(.*?)</li>', re.S)
_RE_SNIPPET = re.compile(r'lister__description[^>]*>(.*?)</p>', re.S)
_RE_AGO = re.compile(r'job-actions__action pipe">\s*(.*?)\s*</li>', re.S)


def _posted(text: str) -> str:
    """'10 days ago' -> an ISO date. THE never gives the date itself."""
    m = re.match(r"(\d+)\s+(minute|hour|day|week)s?\s+ago", text or "", re.I)
    if not m:
        return date.today().isoformat() if re.search(r"today|just now", text or "", re.I) else ""
    n, unit = int(m.group(1)), m.group(2).lower()
    days = {"minute": 0, "hour": 0, "day": n, "week": n * 7}[unit]
    return (date.today() - timedelta(days=days)).isoformat()


def parse(text: str) -> list[Posting]:
    out: list[Posting] = []
    for chunk in (text or "").split(_CARD_SPLIT)[1:]:
        m_id = _RE_ID.search(chunk)
        href = _RE_HREF.search(chunk)
        title = _RE_TITLE.search(chunk)
        if not m_id or not href or not title:
            continue
        clean_title = clean_html(title.group(1))
        if not clean_title:
            continue
        loc = _RE_LOC.search(chunk)
        salary = _RE_SALARY.search(chunk)
        employer = _RE_EMPLOYER.search(chunk)
        snippet = _RE_SNIPPET.search(chunk)
        ago = _RE_AGO.search(chunk)
        out.append(Posting(
            source=NAME,
            title=clean_title,
            company=clean_html(employer.group(1)) if employer else "",
            location=clean_html(loc.group(1)) if loc else "",
            url=BASE + href.group(1),
            external_id=m_id.group(1),
            posted_at=_posted(clean_html(ago.group(1))) if ago else "",
            compensation=clean_html(salary.group(1)) if salary else "",
            description=clean_html(snippet.group(1)) if snippet else "",
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        kw = quote_plus(str(q.get("keywords", "")))
        pages = int(q.get("pages", 1) or 1)
        for page in range(1, pages + 1):
            url = f"{SEARCH}?keywords={kw}&sort=Date&page={page}"
            status, text = fetcher.get(url, headers={"Accept": "text/html"},
                                       host_delay=1.0)
            # THE answers 404 for a search with no results, which is not a
            # failure: a rare keyword is simply empty.
            if status == 404:
                break
            if status != 200:
                fetcher.errors.append(f"theunijobs: HTTP {status} for '{q.get('keywords')}'")
                break
            got = parse(text)
            out.extend(got)
            if len(got) < PAGE_SIZE:
                break
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
