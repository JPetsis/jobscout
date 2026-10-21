"""Dice — dice.com, the US tech board.

robots.txt disallows the `/job` prefix, and by this project's rule a robots file
is a wish rather than an enforcement: what decides is whether the site can be
read at all. Dice can. It answers 200 from nginx (no Cloudflare challenge) to a
plain client, the search page is server-rendered, and `&page=N` walks it — page
two shared exactly one card with page one when measured.

A card carries the title, the employer, the place, the commitment and often a
salary band, so one request per page of 33 roles is the whole discovery cost.
The job page carries a schema.org JobPosting with the full advert and a salary
band of its own, which the scan's detail probe reads.

Measured 2026-10-21: 33 cards a page for q=software engineer / New York, 366 KB.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import urllib.parse
from datetime import date, timedelta

from ..models import Posting
from ..textutil import clean_html_lines

NAME = "dice"
LABEL = "Dice"
BASE = "https://www.dice.com"
LIST = BASE + "/jobs?q={q}&location={loc}&page={page}"

_CARD_START = 'data-testid="job-card"'
_LINK = re.compile(r'href="(/job-detail/[0-9a-fA-F-]{36})"')
_TITLE = re.compile(r'data-testid="job-search-job-detail-link"[^>]*>(.*?)</a>', re.S)
_COMPANY = re.compile(r'data-testid="job-card-company-name"[^>]*>(.*?)</p>', re.S)
#: Sponsored cards use a second layout whose employer <p> has no test id, only a
#: title attribute. Without this fallback the employer is read as the place.
_COMPANY_TITLE = re.compile(r'<p(?![^>]*data-testid)[^>]*\stitle="([^"]{2,80})"[^>]*>', re.S)
_GUID = re.compile(r'data-job-guid="([^"]+)"')
_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_SALARY = re.compile(r"(?:[$€£]\s?\d|\b(?:USD|EUR|GBP)\s?\d)\d?", re.I)
#: Dice prints ages compactly: "15d ago", "3h ago", "2w ago", besides "Today".
_AGE = re.compile(r"^(today|yesterday|\d+\+?\s*[hdwm]\s+ago|"
                  r"\d+\+?\s*(?:hour|day|week|month)s?\s+ago|"
                  r"\d{1,2}/\d{1,2}/\d{2,4})$", re.I)
#: A band with no currency at all: "180000 - 250000". Both sides are long enough
#: that a year cannot match by accident.
_BARE_BAND = re.compile(r"^\d{3,}[\d,]*\s*[-–]\s*\d{3,}[\d,]*$")
_DAYS = {"hour": 1 / 24, "day": 1, "week": 7, "month": 30}
_COMMITMENT = {"full-time", "part-time", "contract", "internship", "temporary",
               "per diem", "freelance"}


def _text(fragment: str) -> str:
    return htmllib.unescape(
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _lines(fragment: str) -> list[str]:
    """Text runs between tags, one per line: tags become line breaks, not spaces."""
    text = htmllib.unescape(re.sub(r"<[^>]+>", "\n", fragment or ""))
    return [re.sub(r"\s+", " ", p).strip() for p in text.split("\n") if p.strip()]


def _posted_at(text: str) -> str:
    """"Today", "Yesterday", "3 days ago" as a date; anything else stays empty."""
    value = (text or "").strip().lower()
    if value == "today":
        return date.today().isoformat()
    if value == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    m = re.match(r"(\d+)\+?\s*([hdwm])\s+ago", value)
    if m:
        unit = {"h": "hour", "d": "day", "w": "week", "m": "month"}[m.group(2)]
        return (date.today() - timedelta(days=_DAYS[unit] * int(m.group(1)))).isoformat()
    m = re.match(r"(\d+)\+?\s*(hour|day|week|month)s?\s+ago", value)
    if m:
        return (date.today() - timedelta(days=_DAYS[m.group(2)] * int(m.group(1)))).isoformat()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})$", value)
    if m:
        month, day, year = (int(x) for x in m.groups())
        year = year + 2000 if year < 100 else year
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""
    return ""


def _cards(html: str) -> list[str]:
    """Whole card elements: each starts at the tag holding the test id."""
    out: list[str] = []
    pos = 0
    while True:
        found = (html or "").find(_CARD_START, pos)
        if found < 0:
            return out
        start = html.rfind("<", 0, found)
        following = html.find(_CARD_START, found + len(_CARD_START))
        end = html.rfind("<", 0, following) if following >= 0 else len(html)
        out.append(html[start:end])
        if following < 0:
            return out
        pos = following


def _card(chunk: str) -> Posting | None:
    link = _LINK.search(chunk)
    if not link:
        return None
    title_m = _TITLE.search(chunk)
    title = _text(title_m.group(1)) if title_m else ""
    if not title:
        return None
    company_m = _COMPANY.search(chunk) or _COMPANY_TITLE.search(chunk)
    company = _text(company_m.group(1)) if company_m else ""
    guid = _GUID.search(chunk)

    title_key = re.sub(r"\s+", " ", title).strip().lower()
    company_key = re.sub(r"\s+", " ", company).strip().lower()
    location, salary, posted_at, snippet = "", "", "", ""
    for line in _lines(chunk):
        low = line.lower().strip()
        if low in ("", "\u2022", "easy apply") or low == title_key or low == company_key:
            continue
        if low in _COMMITMENT:
            continue
        if _AGE.match(low):
            if not posted_at:
                posted_at = _posted_at(low)
            continue
        if len(line) >= 120:
            # a sponsored card carries a paragraph of the advert itself
            if not snippet:
                snippet = line
            continue
        if len(line) <= 60 and (_SALARY.search(line) or _BARE_BAND.match(line)):
            if not salary:
                salary = line
            continue
        if not location and len(line) < 80:
            location = line
    return Posting(
        source=NAME,
        title=title,
        company=company,
        location=location,
        url=BASE + link.group(1),
        external_id=guid.group(1) if guid else link.group(1).rsplit("/", 1)[-1],
        description=snippet,
        compensation=salary,
        posted_at=posted_at,
    )


def parse_cards(html: str) -> list[Posting]:
    """The rendered job cards on one search page, in page order.

    Dice also ships its React flight payload inside <script> tags, and that
    payload contains escaped copies of the same card markup. Stripping scripts
    first keeps the parser on the rendered cards: the payload copies have no
    employer element, so they would arrive as headings with a company-shaped hole.
    """
    out = []
    for chunk in _cards(re.sub(r"<script\b.*?</script>", "", html or "", flags=re.S | re.I)):
        p = _card(chunk)
        if p is not None:
            out.append(p)
    return out


def _place(job_location) -> str:
    places = job_location if isinstance(job_location, list) else [job_location]
    parts = []
    for place in places:
        if not isinstance(place, dict):
            continue
        address = place.get("address") if isinstance(place.get("address"), dict) else {}
        country = address.get("addressCountry") or ""
        if isinstance(country, dict):
            country = country.get("name") or ""
        piece = ", ".join(str(x) for x in (address.get("addressLocality"),
                                           address.get("addressRegion"), country) if x)
        if piece and piece not in parts:
            parts.append(piece)
    return " / ".join(parts)


def _salary(value) -> str:
    """A schema.org MonetaryAmount as text.

    Dice puts minValue/maxValue straight on baseSalary; other boards nest them
    under `value`, so both shapes are read.
    """
    if not isinstance(value, dict):
        return ""
    nested = value.get("value")
    amount = nested if isinstance(nested, dict) else value
    numbers = [n for n in (amount.get("minValue"), amount.get("maxValue"),
                           amount.get("value")) if isinstance(n, (int, float))]
    if not numbers:
        return ""
    currency = str(value.get("currency") or amount.get("currency") or "USD").upper()
    unit = {"YEAR": "yearly", "MONTH": "monthly", "HOUR": "hourly", "DAY": "daily"}.get(
        str(amount.get("unitText") or "").upper(), "")
    lo, hi = min(numbers), max(numbers)
    band = f"{lo:.0f}" if lo == hi else f"{lo:.0f} - {hi:.0f}"
    return f"{currency} {band} {unit}".strip()


def parse_job(html: str, url: str) -> Posting | None:
    """The schema.org JobPosting on a job page, or None when there is none."""
    for raw in _LD.findall(html or ""):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                continue
            org = item.get("hiringOrganization")
            return Posting(
                source=NAME,
                title=str(item.get("title") or "").strip(),
                company=str(org.get("name") or "").strip() if isinstance(org, dict) else "",
                location=_place(item.get("jobLocation")),
                url=url,
                description=clean_html_lines(str(item.get("description") or "")),
                compensation=_salary(item.get("baseSalary")),
                posted_at=str(item.get("datePosted") or "")[:10],
                expires_at=str(item.get("validThrough") or "")[:10],
                extra={"employment": str(item.get("employmentType") or "")},
            )
    return None


def parse(text: str, url: str = "") -> Posting | None:
    """Pure entry point for tests: one job page in, one Posting out."""
    return parse_job(text, url)


def _queries(settings) -> list[dict]:
    queries = settings.get("queries") or []
    if isinstance(queries, dict):
        queries = [queries]
    if not queries:
        queries = [{"keywords": "software engineer", "location": "", "pages": 1}]
    out = []
    for q in queries:
        if not isinstance(q, dict):
            continue
        out.append({
            "keywords": str(q.get("keywords") or q.get("q") or "").strip(),
            "location": str(q.get("location") or "").strip(),
            "pages": max(1, int(q.get("pages", settings.get("max_pages", 1)) or 1)),
        })
    return [q for q in out if q["keywords"]]


def fetch(settings, fetcher) -> list[Posting]:
    """Each query's pages, newest first; duplicates across queries are dropped."""
    out: list[Posting] = []
    seen: set[str] = set()
    for query in _queries(settings):
        for page in range(1, query["pages"] + 1):
            url = LIST.format(q=urllib.parse.quote_plus(query["keywords"]),
                              loc=urllib.parse.quote_plus(query["location"]),
                              page=page)
            status, html = fetcher.get(url)
            if status != 200:
                fetcher.errors.append(f"dice: HTTP {status} for {query['keywords']} page {page}")
                break
            fresh = [p for p in parse_cards(html) if p.url not in seen]
            if not fresh:
                break                 # past the end of the results
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
