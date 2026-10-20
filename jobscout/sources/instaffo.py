"""Instaffo — jobs.instaffo.com, the board behind app.instaffo.com.

The candidate API (app.instaffo.com/api/v1/…) answers
`403 {"errors":{"base":"not_allowed"}}` to an anonymous client: that is a login
gate, not obfuscation, so it is out of bounds. The app shell is `Disallow: /` in
robots.txt as well.

The public half is the board the marketing site links to: jobs.instaffo.com
renders ten job cards per list page (`/de/jobs/find?page=N`), newest first, so
discovering the whole board costs one request per ten roles instead of one per
role. Measured 2026-10-20: about 180 pages for 1,805 roles.

Cards carry the title, the employer, the city, a salary badge when the employer
published one, and a relative age ("vor 16 Stunden"). They carry no description,
so this behaves like every other teaser board: the scan's existing detail probe
completes the rows that scored, from the job page, which is server-rendered with
a schema.org JobPosting.
"""
from __future__ import annotations

import html as htmllib
import json
import re
from datetime import date, timedelta

from ..models import Posting
from ..textutil import clean_html_lines

NAME = "instaffo"
LABEL = "Instaffo"
BASE = "https://jobs.instaffo.com"
LIST = BASE + "/de/jobs/find?page={page}"

_LINK = re.compile(r'<a class="[^"]*"\s+href="(/de/job/[^"?#]+)', re.S)
_TITLE = re.compile(r'data-variant="title-m_bold"[^>]*>(.*?)</h2>', re.S)
_H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
_LOGO = re.compile(r'alt="([^"]+?) logo"')
_PARAGRAPH = re.compile(r'<p class="[^"]*variant_body-m[^"]*"[^>]*>(.*?)</p>', re.S)
#: The city sits in a <p> with no class at all, between the company's size chip
#: ("SMBs • 101-500") and the age; every other paragraph carries a variant class.
_BARE_PARAGRAPH = re.compile(r"<p(?![^>]*class=)[^>]*>(.*?)</p>", re.S)
_BADGE = re.compile(r'salaryBadge[^"]*"[^>]*>(.*?)</div>', re.S)
_AGO = re.compile(r"vor\s+(\d+|einem|einer)\s+(Stunde|Tag|Woche|Monat|Jahr)", re.I)
_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_UNIT = {"YEAR": "jährlich", "MONTH": "monatlich", "HOUR": "stündlich",
         "DAY": "täglich", "WEEK": "wöchentlich"}
_DAYS = {"stunde": 1 / 24, "tag": 1, "woche": 7, "monat": 30, "jahr": 365}


def _text(fragment: str) -> str:
    # "Werkstudent:in Product &amp; Customer Operations" is one card's real title
    return htmllib.unescape(
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _posted_at(fragment: str) -> str:
    """"vor 16 Stunden" as a date. Relative on purpose: the card states no date."""
    m = _AGO.search(_text(fragment))
    if not m:
        return ""
    n = 1 if m.group(1).lower() in ("einem", "einer") else int(m.group(1))
    return (date.today() - timedelta(days=_DAYS[m.group(2).lower()] * n)).isoformat()


def _card(fragment: str, url: str) -> Posting | None:
    m = _TITLE.search(fragment) or _H2.search(fragment)
    title = _text(m.group(1)) if m else ""
    if not title:
        return None
    paragraphs = [_text(p) for p in _PARAGRAPH.findall(fragment)]
    bare = [_text(p) for p in _BARE_PARAGRAPH.findall(fragment)]
    logo = _LOGO.search(fragment)
    company = _text(logo.group(1)) if logo else (paragraphs[0] if paragraphs else "")
    location = bare[0] if bare else (paragraphs[1] if len(paragraphs) > 1 else "")
    badge = _BADGE.search(fragment)
    return Posting(
        source=NAME,
        title=title,
        company=company,
        location=location,
        url=url,
        external_id=url.rsplit("-", 1)[-1],
        compensation=_text(badge.group(1)) if badge else "",
        posted_at=_posted_at(fragment),
    )


def parse_cards(html: str) -> list[Posting]:
    """The job cards on one list page, in page order."""
    out = []
    for m in _LINK.finditer(html or ""):
        end = html.find("</a>", m.end())
        fragment = html[m.start(): end if end > 0 else m.end() + 4000]
        p = _card(fragment, BASE + m.group(1))
        if p is not None:
            out.append(p)
    return out


def _salary(value) -> str:
    """A schema.org MonetaryAmount as the text salary.parse understands."""
    if not isinstance(value, dict):
        return ""
    amount = value.get("value")
    if not isinstance(amount, dict):
        amount = {"value": amount} if amount is not None else {}
    low, high = amount.get("minValue"), amount.get("maxValue")
    single = amount.get("value")
    numbers = [n for n in (low, high, single) if isinstance(n, (int, float))]
    if not numbers:
        return ""
    currency = str(value.get("currency") or "").upper() or "EUR"
    unit = _UNIT.get(str(amount.get("unitText") or "").upper(), "")
    lo, hi = min(numbers), max(numbers)
    band = f"{lo:.0f}" if lo == hi else f"{lo:.0f} – {hi:.0f}"
    return f"{band} {currency} {unit}".strip()


def _place(job_location) -> str:
    """City and country from jobLocation, which is an object or a list of them."""
    places = job_location if isinstance(job_location, list) else [job_location]
    parts = []
    for place in places:
        if not isinstance(place, dict):
            continue
        raw_address = place.get("address")
        address = raw_address if isinstance(raw_address, dict) else {}
        city = address.get("addressLocality") or ""
        region = address.get("addressRegion") or ""
        country = address.get("addressCountry") or ""
        if isinstance(country, dict):                  # schema.org allows {"name": …}
            country = country.get("name") or country.get("addressCountry") or ""
        piece = ", ".join(str(x) for x in (city, region, country) if x)
        if piece and piece not in parts:
            parts.append(piece)
    return " / ".join(parts)


def _posting(job: dict, url: str) -> Posting | None:
    title = str(job.get("title") or "").strip()
    if not title:
        return None
    org = job.get("hiringOrganization")
    company = str((org or {}).get("name") or "").strip() if isinstance(org, dict) else ""
    ident = job.get("identifier")
    external_id = ""
    if isinstance(ident, dict):
        external_id = str(ident.get("value") or "")
    elif ident:
        external_id = str(ident)
    return Posting(
        source=NAME,
        title=title,
        company=company,
        location=_place(job.get("jobLocation")),
        url=url,
        external_id=external_id,
        description=clean_html_lines(str(job.get("description") or "")),
        compensation=_salary(job.get("baseSalary")),
        posted_at=str(job.get("datePosted") or "")[:10],
        expires_at=str(job.get("validThrough") or "")[:10],
    )


def parse_job(html: str, url: str) -> Posting | None:
    """The schema.org JobPosting on a job page, or None when there is none."""
    for raw in _LD.findall(html or ""):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return _posting(item, url)
    return None


def parse(text: str, url: str = "") -> Posting | None:
    """Pure entry point for tests: one job page in, one Posting out."""
    return parse_job(text, url)


def fetch(settings, fetcher) -> list[Posting]:
    """The newest `max_pages` list pages, ten cards each, newest first."""
    max_pages = max(1, int(settings.get("max_pages", 10) or 10))
    out: list[Posting] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        status, html = fetcher.get(LIST.format(page=page))
        if status != 200:
            fetcher.errors.append(f"instaffo: HTTP {status} for page {page}")
            break
        fresh = [p for p in parse_cards(html) if p.url not in seen]
        if not fresh:
            break                    # past the end of the list
        seen.update(p.url for p in fresh)
        out.extend(fresh)
    return out


def fetch_detail(p: Posting, fetcher) -> str | None:
    """The full advert, for the scan's detail probe."""
    status, html = fetcher.get(p.url)
    if status != 200:
        return None
    got = parse_job(html, p.url)
    return got.description if got else None
