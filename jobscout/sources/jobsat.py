"""jobs.at — Austria's largest job board, server-rendered search results.

The public search at ``/j`` is a plain GET (the same form a browser submits)
and the whole result list arrives in the initial HTML, so no browser, token or
JSON endpoint is involved. Cards are ``<li data-job="{id}">`` elements; the
detail URL is base64-encoded in ``data-c-redirect``.

Deliberately Austria-only: a card without a location anchor is still Austrian
(several employers omit it), so an empty location becomes "Österreich" rather
than dropping the posting.
"""
from __future__ import annotations

import base64
import binascii
import html
import re
import urllib.parse

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html

NAME = "jobsat"
LABEL = "jobs.at"
SEARCH = "https://www.jobs.at/j"
PER_PAGE = 15

_CARD = re.compile(r'<li[^>]*\bdata-job="(\d+)"[^>]*>.*?</li>', re.S)
_TITLE = re.compile(r'data-c-title="([^"]*)"')
_DATE = re.compile(r'data-job-date="(\d+)"')
_REDIRECT = re.compile(r'data-c-redirect="([^"]*)"')
_GTM_COMPANY = re.compile(r'data-gtm-element-detail="([^"]*)"')
_LOGO = re.compile(r'<img[^>]*\balt="Logo ([^"]*)"')
_COMPANY_LINK = re.compile(r'data-job-company[^>]*>(.*?)</a>', re.S)
_LOCATIONS = re.compile(r'<a[^>]*class="[^"]*js-locationLink[^"]*"[^>]*>([^<]+)</a>')
_HAS_MORE = re.compile(r'data-has-more="([^"]*)"')


def _text(markup: str) -> str:
    return re.sub(r"\s+", " ", clean_html(markup)).strip()


def _company(card: str) -> str:
    """Employer name: the GTM attribute is cleanest, logo alt and link are fallbacks."""
    for rx in (_GTM_COMPANY, _LOGO):
        m = rx.search(card)
        if m:
            name = html.unescape(m.group(1)).strip()
            if name:
                return name
    m = _COMPANY_LINK.search(card)
    if m:
        name = _text(m.group(1))
        if name:
            return name
    return ""


def _detail_url(card: str, job_id: str) -> str:
    m = _REDIRECT.search(card)
    if m:
        try:
            raw = base64.b64decode(html.unescape(m.group(1))).decode("utf-8", "replace")
            if raw.startswith("http"):
                return raw
        except (binascii.Error, ValueError):
            pass
    return f"https://www.jobs.at/i/{job_id}"


def parse(body: str) -> list[Posting]:
    """Parse one search-results page into postings."""
    out: list[Posting] = []
    for m in _CARD.finditer(body or ""):
        job_id = m.group(1)
        card = m.group(0)

        t = _TITLE.search(card)
        if not t:
            continue
        title = html.unescape(t.group(1)).strip()
        if not title:
            continue

        locations = [html.unescape(x).strip() for x in _LOCATIONS.findall(card)]
        locations = [x for x in locations if x]
        # a card with no location anchor is still an Austrian posting
        location = ", ".join(dict.fromkeys(locations)) or "Österreich"

        d = _DATE.search(card)
        posted = ""
        if d:
            import datetime as _dt
            posted = _dt.datetime.fromtimestamp(int(d.group(1)),
                                                _dt.timezone.utc).strftime("%Y-%m-%d")

        out.append(Posting(
            source=NAME,
            title=title,
            company=_company(card),
            location=location,
            url=_detail_url(card, job_id),
            external_id=job_id,
            posted_at=posted,
            extra={"query_location": "Österreich"},
        ))
    return out


def has_more(body: str) -> bool:
    m = _HAS_MORE.search(body or "")
    return bool(m and m.group(1).strip() not in ("", "0", "false"))


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    queries = settings.get("queries") or [{}]
    max_pages = int(settings.get("max_pages", 2))
    max_age = int(settings.get("max_age_days", 45))

    for q in queries:
        if not isinstance(q, dict):
            continue
        keyword = (q.get("keyword") or "").strip()
        location = (q.get("location") or "").strip()
        params: dict[str, object] = {}
        if keyword:
            params["keyword"] = keyword
        if location:
            params["location"] = location
        base = SEARCH + "?" + urllib.parse.urlencode(params) if params else SEARCH

        for page in range(1, max_pages + 1):
            # one page parameter only: a duplicated `page` makes the server read
            # the first one, so the loop would refetch page 1 forever (and 404
            # on some keywords)
            url = f"{base}{'&' if base.endswith('?') or '?' in base else '?'}page={page}"
            try:
                status, body = fetcher.get(url)
            except Exception as e:
                fetcher.errors.append(f"jobsat/{keyword or 'all'}: {e}")
                break
            if status == 404 and page > 1:
                # jobs.at 404s a page beyond the last one even though its
                # data-has-more flag still says "1" (seen on keyword=PHP, which
                # has exactly one page). That is the end of the results, not a
                # fetch failure.
                break
            if status != 200:
                fetcher.errors.append(f"jobsat/{keyword or 'all'}: HTTP {status}")
                break

            page_items = parse(body)
            out.extend(page_items)

            # stop once the page is entirely older than the age window
            dates = [p.posted_at for p in page_items if p.posted_at]
            if dates and max(dates) < _cutoff(max_age):
                break
            if not page_items or not has_more(body):
                break
    return out


def _cutoff(max_age_days: int) -> str:
    import datetime as _dt
    return (_dt.date.today() - _dt.timedelta(days=max_age_days)).isoformat()
