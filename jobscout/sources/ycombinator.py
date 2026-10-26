"""Y Combinator — ycombinator.com/jobs, the public half of Work at a Startup.

robots.txt allows everything except /verify and library query strings, and the
role pages are server-rendered: /jobs/role/<role> lists the roles added recently
with employer, track, salary and place inline, and a city path narrows it
(/jobs/role/software-engineer/new-york). Each job page carries a schema.org
JobPosting, so the scan's detail probe has the full advert.

Measured 2026-10-21: 39 software-engineer roles on the role page in a 33 KB
response. The list is "added recently", which is what a daily scan wants; it is
not the whole archive, and the archive sits behind Work at a Startup's login.
"""
from __future__ import annotations

import html as htmllib
import json
import re
from datetime import date, timedelta

from ..models import Posting
from ..textutil import clean_html_lines

NAME = "ycombinator"
LABEL = "Y Combinator"
BASE = "https://www.ycombinator.com"
LIST = BASE + "/jobs/role/{role}"

_CARD_START = '<li class="my-2'
_JOB = re.compile(r'<a href="(/companies/[^"]+/jobs/[^"]+)"[^>]*>(.*?)</a>', re.S)
_COMPANY = re.compile(r'<span class="block font-bold[^"]*">(.*?)</span>', re.S)
_AGO = re.compile(r"(?:about\s+)?(\d+|a|an)\s+(hour|day|week|month|year)s?", re.I)
_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
# The page ships its own data: an Inertia payload with one object per card, and
# it carries the field the markup does not -- `createdAt`, the age of the JOB.
_PAGE = re.compile(r'data-page="([^"]+)"')
_BULLET = "\u2022"
_SALARY = re.compile(r"[$€£]\s?\d")
_EMPLOYMENT = {"full-time", "part-time", "contract", "internship", "temporary"}
_DAYS = {"hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}


def _text(fragment: str) -> str:
    return htmllib.unescape(
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "\n", fragment or ""))).strip()


def _lines(fragment: str) -> list[str]:
    """Text runs between tags, one per line: tags become line breaks, not spaces."""
    text = htmllib.unescape(re.sub(r"<[^>]+>", "\n", fragment or ""))
    return [re.sub(r"\s+", " ", p).strip() for p in text.split("\n") if p.strip()]


def _posted_at(text: str) -> str:
    """'3 days' / 'about 21 hours' / 'a month' / 'about 4 years' -> an ISO date."""
    m = _AGO.search(text or "")
    if not m:
        return ""
    n = 1 if m.group(1).lower() in ("a", "an") else int(m.group(1))
    return (date.today() - timedelta(days=_DAYS[m.group(2).lower()] * n)).isoformat()


def _listing_rows(html: str) -> list[dict]:
    """The Inertia payload: every card's own data, dates included.

    Found by reading the page rather than by guessing: each object carries
    `createdAt` (how old the JOB is: "3 months", "about 4 years") and
    `lastActive` (how recently it was touched). The visible "(about 21 hours
    ago)" is lastActive, which is why a role posted in 2020 could look new.
    """
    match = _PAGE.search(html or "")
    if not match:
        return []
    try:
        data = json.loads(htmllib.unescape(match.group(1)))
    except ValueError:
        return []

    def walk(node):
        if isinstance(node, dict):
            if {"title", "companyName", "createdAt"} <= set(node):
                yield node
                return
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    return list(walk(data))


def _from_row(row: dict) -> Posting | None:
    title = str(row.get("title") or "").strip()
    url = str(row.get("url") or "").strip()
    if not title or not url:
        return None
    if url.startswith("/"):
        url = BASE + url
    return Posting(
        source=NAME,
        title=title,
        company=str(row.get("companyName") or "").strip(),
        location=str(row.get("location") or "").strip(),
        url=url,
        external_id=str(row.get("id") or "") or url.rsplit("/", 1)[-1].split("-", 1)[0],
        compensation=str(row.get("salaryRange") or "").strip(),
        posted_at=_posted_at(str(row.get("createdAt") or "")),
    )


def _cards(html: str) -> list[str]:
    """Split the page into per-job chunks; cards are sibling list items."""
    chunks = (html or "").split(_CARD_START)
    return [_CARD_START + c for c in chunks[1:]]


def _card(chunk: str) -> Posting | None:
    job = _JOB.search(chunk)
    if not job:
        return None
    title = _text(job.group(2))
    if not title:
        return None
    company = _COMPANY.search(chunk)
    lines = _lines(chunk)
    # the meta line follows the title: Full-time • Engineering • Full stack •
    # $185K - $485K • San Francisco, CA
    salary, location = "", ""
    tokens = [t.strip() for part in lines for t in part.split(_BULLET) if t.strip()]
    for i, token in enumerate(tokens):
        if _SALARY.search(token) and not salary:
            salary = token
            location = tokens[i + 1] if i + 1 < len(tokens) else ""
            break
    if not location:
        # cards without a published band: the place is the token after the track
        tail = [t for t in tokens if t.lower() not in _EMPLOYMENT and _SALARY.search(t) is None]
        location = tail[-1] if tail else ""
    # the apply button's label sits inside the same text run
    location = re.sub(r"\s+(?:Apply|Save)$", "", location).strip()
    return Posting(
        source=NAME,
        title=title,
        company=_text(company.group(1)) if company else "",
        location=location,
        url=BASE + job.group(1),
        external_id=job.group(1).rsplit("/", 1)[-1].split("-", 1)[0],
        compensation=salary,
        posted_at="",   # the markup's age is the feed's activity, not the posting
    )


def parse_cards(html: str) -> list[Posting]:
    """The job cards on one role page, in page order.

    The payload is preferred because it dates the posting. The markup fallback
    keeps the board working if that payload changes, but it deliberately leaves
    `posted_at` empty: the only date in the markup is the feed's activity, and
    claiming a role posted in 2020 is fresh is exactly the bug this avoided.
    """
    rows = _listing_rows(html)
    if rows:
        out = [p for p in (_from_row(row) for row in rows) if p is not None]
        if out:
            return out
    out = []
    for chunk in _cards(html):
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
    """One request per role (and city), newest first; no pagination to walk."""
    roles = settings.get("roles") or ["software-engineer"]
    if isinstance(roles, str):
        roles = [roles]
    places = settings.get("locations") or []
    if isinstance(places, str):
        places = [places]
    max_jobs = max(1, int(settings.get("max_jobs", 120) or 120))
    out: list[Posting] = []
    seen: set[str] = set()
    for role in roles:
        for place in [""] + list(places):
            path = LIST.format(role=role) + (f"/{place}" if place else "")
            status, html = fetcher.get(path)
            if status != 200:
                fetcher.errors.append(f"ycombinator: HTTP {status} for {path}")
                continue
            for p in parse_cards(html):
                if p.url not in seen:
                    seen.add(p.url)
                    out.append(p)
            if len(out) >= max_jobs:
                return out[:max_jobs]
    return out[:max_jobs]


def fetch_detail(p: Posting, fetcher) -> str | None:
    """The full advert, for the scan's detail probe."""
    status, html = fetcher.get(p.url)
    if status != 200:
        return None
    got = parse_job(html, p.url)
    return got.description if got else None
