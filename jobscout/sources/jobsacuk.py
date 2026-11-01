"""jobs.ac.uk — the UK higher-education job board, server-rendered search.

The page is plain HTML with no key and no challenge: `/search/?keywords=…` with
`pageSize` and `startIndex`, where startIndex is an OFFSET into the result list
(1, 26, 51 …), not a page number. Verified live: `startIndex=2` returns the
second card of page one, and `startIndex=26` is empty on a 13-result search.

Each card carries what the sector cares about and no other board does: the
department, the employer, the salary band, the date placed and the CLOSING
date ("Closes 02 Nov"). The closing date becomes `expires_at`, so a posting
whose deadline has passed is judged dead by the store without a request.

The site omits the year on both dates; it is inferred against today.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import quote_plus

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html

NAME = "jobsacuk"
LABEL = "jobs.ac.uk"
BASE = "https://www.jobs.ac.uk"
SEARCH = f"{BASE}/search/"
PAGE_SIZE = 25

_CARD_SPLIT = 'class="j-search-result__result'
_RE_ID = re.compile(r'data-advert-id="(\d+)"')
_RE_LINK = re.compile(r'<a href="(/job/[^"]+)"[^>]*>\s*(.*?)\s*</a>', re.S)
_RE_EMPLOYER = re.compile(r'j-search-result__employer[^>]*>\s*<b>(.*?)</b>', re.S)
_RE_DEPT = re.compile(r'j-search-result__department[^>]*>\s*(.*?)\s*</div>', re.S)
_RE_LOC = re.compile(r'Location:\s*([^<]+)', re.S)
_RE_SALARY = re.compile(r'Salary:\s*</strong>\s*([^<]+)', re.S)
_RE_PLACED = re.compile(r'Date Placed:\s*</strong>\s*([^<]+)', re.S)
_RE_CLOSES = re.compile(
    r'Closes</span>.*?j-search-result__date--blue[^>]*>\s*([^<]+)', re.S)


def _ymd(text: str, *, closing: bool) -> str:
    """'15 Sep' -> an ISO date, using the year the site leaves out.

    A placed date in the future means last year; a closing date months in the
    past means next year. Both are inferred, never guessed beyond that.
    """
    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]{3})", text or "")
    if not m:
        return ""
    today = date.today()
    try:
        d = datetime.strptime(f"{m.group(1)} {m.group(2)} {today.year}",
                              "%d %b %Y").date()
    except ValueError:
        return ""
    if closing:
        if (today - d).days > 180:
            d = d.replace(year=d.year + 1)
    elif d > today:
        d = d.replace(year=d.year - 1)
    return d.isoformat()


def parse(text: str) -> list[Posting]:
    out: list[Posting] = []
    for chunk in (text or "").split(_CARD_SPLIT)[1:]:
        m_id = _RE_ID.search(chunk)
        link = _RE_LINK.search(chunk)
        if not m_id or not link:
            continue
        title = clean_html(link.group(2))
        if not title:
            continue
        employer = _RE_EMPLOYER.search(chunk)
        dept = _RE_DEPT.search(chunk)
        loc = _RE_LOC.search(chunk)
        salary = _RE_SALARY.search(chunk)
        placed = _RE_PLACED.search(chunk)
        closes = _RE_CLOSES.search(chunk)
        company = clean_html(employer.group(1)) if employer else ""
        if dept:
            # The department is what tells two lectureships at one university
            # apart, so it goes next to the employer rather than being dropped.
            department = clean_html(dept.group(1))
            company = f"{company} · {department}" if company else department
        out.append(Posting(
            source=NAME,
            title=title,
            company=company,
            location=clean_html(loc.group(1)) if loc else "",
            url=BASE + link.group(1),
            external_id=m_id.group(1),
            posted_at=_ymd(clean_html(placed.group(1)), closing=False) if placed else "",
            expires_at=_ymd(clean_html(closes.group(1)), closing=True) if closes else "",
            compensation=clean_html(salary.group(1)) if salary else "",
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        kw = quote_plus(str(q.get("keywords", "")))
        pages = int(q.get("pages", 1) or 1)
        for page in range(1, pages + 1):
            start = 1 + (page - 1) * PAGE_SIZE
            url = f"{SEARCH}?keywords={kw}&pageSize={PAGE_SIZE}&startIndex={start}"
            status, text = fetcher.get(url, headers={"Accept": "text/html"},
                                       host_delay=1.0)
            if status != 200:
                fetcher.errors.append(f"jobsacuk: HTTP {status} for '{q.get('keywords')}'")
                break
            got = parse(text)
            out.extend(got)
            if len(got) < PAGE_SIZE:
                break       # last page
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
