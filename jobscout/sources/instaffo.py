"""Instaffo — jobs.instaffo.com, the public job board behind app.instaffo.com.

The candidate app (app.instaffo.com) says `Disallow: /` in robots.txt and its
`/api/` is off-limits too, so this reads what the site itself advertises for
crawlers: `sitemap-jobs.xml` lists every live job twice (a `/de/` and an `/en/`
URL per job), and each job page is server-rendered with a schema.org
`JobPosting` carrying the description, the employer, the place, the posting date,
the valid-through date and sometimes a salary band.

Walking all of them is ~1,800 page fetches (measured 2026-10-20: 3,610 URLs,
1,805 jobs), so a scan takes the newest `max_jobs` by the sitemap's `lastmod`:
the board's freshest roles, bounded, the same shape himalayas uses with its
newest pages. Older roles are reachable by raising the cap.
"""
from __future__ import annotations

import json
import re

from ..http import FetchError
from ..models import Posting
from ..textutil import clean_html_lines

NAME = "instaffo"
LABEL = "Instaffo"
SITEMAP = "https://jobs.instaffo.com/sitemap-jobs.xml"

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>\s*(?:<lastmod>\s*([^<\s]+)\s*</lastmod>)?", re.S)
_JOB = re.compile(r"https://jobs\.instaffo\.com/(de|en)/job/([^/?#]+)")
_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_UNIT = {"YEAR": "jährlich", "MONTH": "monatlich", "HOUR": "stündlich",
         "DAY": "täglich", "WEEK": "wöchentlich"}


def parse_sitemap(text: str) -> list[tuple[str, str, str]]:
    """(lastmod, job_id, url) for every job, one URL each, newest first.

    One job has two URLs (German and English). The German page is preferred and
    used as the canonical link, but the cap is applied to JOBS, not to URLs, or
    half of it would be spent on translations of the same roles.
    """
    jobs: dict[str, dict[str, str]] = {}
    for loc, lastmod in _LOC.findall(text or ""):
        m = _JOB.match(loc)
        if not m:
            continue
        lang, jid = m.group(1), m.group(2)
        rec = jobs.setdefault(jid, {"lastmod": "", "de": "", "en": ""})
        rec[lang] = loc
        rec["lastmod"] = max(rec["lastmod"], lastmod or "")
    out = [(rec["lastmod"], jid, rec["de"] or rec["en"]) for jid, rec in jobs.items()]
    out.sort(key=lambda r: (r[0], r[2]), reverse=True)
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
    max_jobs = max(1, int(settings.get("max_jobs", 60) or 60))
    status, text = fetcher.get(SITEMAP)
    if status != 200:
        raise FetchError(SITEMAP, f"HTTP {status}")
    out: list[Posting] = []
    for _lastmod, _jid, url in parse_sitemap(text)[:max_jobs]:
        status, html = fetcher.get(url)
        if status != 200:
            fetcher.errors.append(f"instaffo: HTTP {status} for {url}")
            continue
        p = parse_job(html, url)
        if p is not None:
            out.append(p)
    return out
