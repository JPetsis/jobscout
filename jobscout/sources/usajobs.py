"""USAJOBS — US federal job announcements (requires a free Authorization-Key).

A KEYED source like adzuna: no key means no data. The API answers HTTP 401
with {"title": "Unauthorized", "status": 401} instead of jobs (observed live),
so the adapter refuses loudly — a clear `fetcher.errors` entry naming
`sources.usajobs.key` and `sources.usajobs.email` plus the free portal URL —
instead of returning [] silently.

Every request MUST carry three headers, exactly:
  Host: data.usajobs.gov
  User-Agent: <your email address>    (a real address; USAJOBS uses it to
                                       contact you and requests without it
                                       are rejected by the CDN first)
  Authorization-Key: <key>

Free key: https://developer.usajobs.gov/apirequest/ — a form; the key arrives
by email. Public job announcements only; ResultsPerPage caps at 500.

Config:
  sources:
    usajobs:
      key: ...
      email: you@example.com
      keyword: software engineer   # optional
      results_per_page: 25         # max 500, default 25
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from ..http import FetchError, Fetcher
from ..models import Posting
from ..textutil import clean_html, date_part

NAME = "usajobs"
LABEL = "USAJOBS"
API = "https://data.usajobs.gov/api/search"
KEY_URL = "https://developer.usajobs.gov/apirequest/"
MAX_PER_PAGE = 500

# RateIntervalCode -> the period word the salary parser understands. Codes
# from the live /api/codelist/remunerationrateintervalcodes: PA=Per Year,
# PM=Per Month, PD=Per Day, PH=Per Hour. BW (bi-weekly), FB, PW, ST, SY, WC
# have no parsable period and are left out rather than guessed.
_PERIOD = {"PA": "year", "PM": "month", "PD": "day", "PH": "hour"}


def _compensation(desc: dict) -> str:
    """First PositionRemuneration entry as 'MIN-MAX USD per <period>'."""
    rem = desc.get("PositionRemuneration")
    r = rem[0] if isinstance(rem, list) and rem and isinstance(rem[0], dict) else None
    if not r:
        return ""
    lo = str(r.get("MinimumRange") or "").strip()
    hi = str(r.get("MaximumRange") or "").strip()
    if not lo and not hi:
        return ""
    amount = f"{lo}-{hi}" if lo and hi and lo != hi else (lo or hi)
    parts = [amount, "USD"]             # US federal pay is denominated in USD
    period = _PERIOD.get(str(r.get("RateIntervalCode") or "").upper())
    if period:
        parts.append(f"per {period}")
    return " ".join(parts)


def parse(data: dict) -> list[Posting]:
    if not isinstance(data, dict):
        return []
    result = data.get("SearchResult")
    items = result.get("SearchResultItems") if isinstance(result, dict) else None
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        desc = it.get("MatchedObjectDescriptor")
        if not isinstance(desc, dict):
            continue
        locs = [str(l.get("LocationName") or "").strip()
                for l in (desc.get("PositionLocation") or [])
                if isinstance(l, dict)]
        ua = desc.get("UserArea")
        ua = ua if isinstance(ua, dict) else {}
        details = ua.get("Details")
        details = details if isinstance(details, dict) else {}
        out.append(Posting(
            source=NAME,
            title=str(desc.get("PositionTitle") or "").strip(),
            company=str(desc.get("OrganizationName") or "").strip(),
            location=", ".join(x for x in locs if x),
            url=str(desc.get("PositionURI") or "").strip(),
            external_id=str(it.get("MatchedObjectId") or ""),
            posted_at=date_part(str(desc.get("PositionStartDate") or "")),
            description=clean_html(str(details.get("JobSummary") or ""))[:8000],
            compensation=_compensation(desc),
            extra={"department": desc.get("DepartmentName"),
                   "position_id": desc.get("PositionID")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    missing = [k for k in ("key", "email")
               if not str(settings.get(k) or "").strip()]
    if missing:
        fetcher.errors.append(
            f"usajobs: missing {'/'.join(missing)} — set sources.usajobs.key "
            "and sources.usajobs.email in the config (the email is sent as the "
            "User-Agent and must be a real address); free key: "
            f"{KEY_URL} (arrives by email)"
        )
        return []
    key = str(settings["key"]).strip()
    email = str(settings["email"]).strip()

    params: dict = {
        "ResultsPerPage": min(int(settings.get("results_per_page", 25) or 25),
                              MAX_PER_PAGE),
        "Page": 1,
    }
    keyword = str(settings.get("keyword") or "").strip()
    if keyword:
        params["Keyword"] = keyword
    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": email,            # the User-Agent IS the email address
        "Authorization-Key": key,
        "Accept": "application/json",
    }
    url = f"{API}?{urlencode(params)}"

    try:
        status, text = fetcher.get(url, headers=headers)
    except FetchError as e:
        fetcher.errors.append(f"usajobs: network failure: {e.message}")
        return []
    except Exception as e:
        fetcher.errors.append(f"usajobs: {type(e).__name__}: {e}")
        return []

    if status == 401:
        fetcher.errors.append(
            "usajobs: HTTP 401 Unauthorized — the Authorization-Key was "
            "REJECTED. Check sources.usajobs.key, and that "
            "sources.usajobs.email is the address the key was issued to "
            f"(free key: {KEY_URL})"
        )
        return []
    if status != 200:
        fetcher.errors.append(f"usajobs: HTTP {status}")
        return []

    try:
        data = json.loads(text)
    except ValueError as e:
        fetcher.errors.append(f"usajobs: invalid JSON: {e}")
        return []
    return parse(data)
