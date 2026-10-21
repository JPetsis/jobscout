"""Wellfound (formerly AngelList Talent) — wellfound.com.

robots.txt disallows only /_jobs/ and two sign-in query forms, and the role
pages are server-rendered: /role/r/<role>?page=N returns about 41 cards per page.
Measured 2026-10-21: 1,899 software-engineer roles over 46 pages, in 86 KB for
page one. Each card carries the employer (as the logo's alt text), the title,
the commitment, a salary band and the place; the job page has a schema.org
JobPosting with the full advert, which the scan's detail probe reads.

The card is a teaser: it has no description, so stack terms are only visible to
the detail probe. That is the same trade every teaser board here makes.
"""
from __future__ import annotations

import html as htmllib
import json
import re
from datetime import date, timedelta

from ..models import Posting
from ..textutil import clean_html_lines

NAME = "wellfound"
LABEL = "Wellfound"
BASE = "https://wellfound.com"
LIST = BASE + "/role/r/{role}?page={page}"

_CARD_START = '<div class="mb-4'
_JOB = re.compile(r'<a[^>]+href="(/jobs/\d+[^"?#]*)"[^>]*>(.*?)</a>', re.S)
_COMPANY = re.compile(r'alt="([^"]{2,80}?) company logo"')
_COMPANY_LINK = re.compile(r'href="/company/[^"]+"[^>]*>([^<]{2,60})<')
_AGO = re.compile(r"(\d+|an?)\s+(hour|day|week|month)s?\s+ago", re.I)
_SALARY = re.compile(r"[$\u20ac\u00a3]\s?\d")
_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_COMMITMENT = {"full-time", "part-time", "contract", "internship", "temporary"}


def _text(fragment: str) -> str:
    return htmllib.unescape(
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "\n", fragment or ""))).strip()


def _lines(fragment: str) -> list[str]:
    """Text runs between tags, one per line: tags become line breaks, not spaces."""
    text = htmllib.unescape(re.sub(r"<[^>]+>", "\n", fragment or ""))
    return [re.sub(r"\s+", " ", p).strip() for p in text.split("\n") if p.strip()]


def _cards(html: str) -> list[str]:
    chunks = (html or "").split(_CARD_START)
    return [_CARD_START + c for c in chunks[1:]]


def _job(job, after: str, company: str) -> Posting | None:
    """One job link plus the text that follows it up to the next role."""
    title = _text(job.group(2))
    if not title:
        return None
    lines = _lines(after)
    salary, location = "", ""
    for i, line in enumerate(lines):
        if _SALARY.search(line) and not salary:
            salary = line
            break
    for line in lines:
        low = line.lower()
        if low in _COMMITMENT or _SALARY.search(line) or "ago" in low or low in ("save", "apply"):
            continue
        if "remote" in low or "onsite" in low or "," in line or " / " in line:
            location = line
            break
    ago = None
    for line in lines:
        ago = _AGO.search(line)
        if ago:
            break
    posted_at = ""
    if ago:
        n = 1 if ago.group(1).lower() in ("a", "an") else int(ago.group(1))
        days = {"hour": 1 / 24, "day": 1, "week": 7, "month": 30}[ago.group(2).lower()]
        posted_at = (date.today() - timedelta(days=days * n)).isoformat()
    return Posting(
        source=NAME,
        title=title,
        company=company,
        location=location,
        url=BASE + job.group(1),
        external_id=job.group(1).rstrip("/").rsplit("/", 1)[-1].split("-", 1)[0],
        compensation=salary,
        posted_at=posted_at,
    )


def parse_cards(html: str) -> list[Posting]:
    """Every job on one role page, in page order.

    A card is a COMPANY block that may hold several roles, so the employer is
    read once per block and each job link inside it is its own posting.
    """
    out = []
    for card in _cards(html):
        company = _COMPANY.search(card) or _COMPANY_LINK.search(card)
        company_name = _text(company.group(1)) if company else ""
        matches = list(_JOB.finditer(card))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(card)
            p = _job(m, card[m.end():end], company_name)
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
    if not isinstance(value, dict):
        return ""
    amount = value.get("value")
    if not isinstance(amount, dict):
        amount = {"value": amount} if amount is not None else {}
    numbers = [n for n in (amount.get("minValue"), amount.get("maxValue"),
                           amount.get("value")) if isinstance(n, (int, float))]
    if not numbers:
        return ""
    currency = str(value.get("currency") or "USD").upper()
    unit = {"YEAR": "yearly", "MONTH": "monthly", "HOUR": "hourly"}.get(
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
                extra={"employment": str(item.get("employmentType") or "")},
            )
    return None


def parse(text: str, url: str = "") -> Posting | None:
    """Pure entry point for tests: one job page in, one Posting out."""
    return parse_job(text, url)


def fetch(settings, fetcher) -> list[Posting]:
    """The first `max_pages` role pages, about 41 cards each."""
    roles = settings.get("roles") or ["software-engineer"]
    if isinstance(roles, str):
        roles = [roles]
    max_pages = max(1, int(settings.get("max_pages", 3) or 3))
    out: list[Posting] = []
    seen: set[str] = set()
    for role in roles:
        for page in range(1, max_pages + 1):
            status, html = fetcher.get(LIST.format(role=role, page=page))
            if status != 200:
                fetcher.errors.append(f"wellfound: HTTP {status} for {role} page {page}")
                break
            fresh = [p for p in parse_cards(html) if p.url not in seen]
            if not fresh:
                break                     # past the end of the results
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
