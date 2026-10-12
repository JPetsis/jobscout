"""Adzuna — multinational job-search API (requires a free app_id + app_key).

This is a KEYED source: it cannot work without credentials, and its two failure
modes must not be mistaken for "no jobs" (both observed live with curl):

  * NO key at all            -> HTTP 400, an HTML error page
  * a WRONG key              -> HTTP 401 carrying {"exception": "AUTH_FAIL"}

So the adapter refuses loudly instead of returning []: a clear `fetcher.errors`
entry names `sources.adzuna.app_id` / `sources.adzuna.app_key` and the free
signup URL. Never a silent empty list.

Free instant key: https://developer.adzuna.com/signup
Free-tier ToS quotas (one "hit" = one request): 25/minute, 250/day, 1000/week,
2500/month — so this adapter sends ONE request per scan unless `pages` is
raised, with `results_per_page` fixed at the API maximum of 50.

`country` is a PATH SEGMENT of the endpoint ('us', 'gb', 'de', 'at', 'ch',
'nl', 'fr', 'au', 'ca', ...) and decides both the job market and the salary
currency. Salaries are annual figures in that local currency; the API ships
only a truncated description snippet, and `salary_is_predicted` flags pay that
Adzuna estimated rather than the employer's own figure (kept in `extra`).

Config:
  sources:
    adzuna:
      app_id: ...
      app_key: ...
      country: de             # path segment, default 'us'
      what: php developer     # search keywords, optional
      where: berlin           # location restriction, optional
      pages: 2                # requests per scan; 1 respects the free quota
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from ..http import FetchError, Fetcher
from ..models import Posting
from ..textutil import clean_html, date_part

NAME = "adzuna"
LABEL = "Adzuna"
API = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
KEY_URL = "https://developer.adzuna.com/signup"
PAGE_SIZE = 50  # the API maximum for results_per_page

# The country path segment decides the salary currency of the response. Only
# countries listed here get a currency token on the compensation string; any
# other country yields pay WITHOUT a currency rather than a guessed one, and
# the salary parser then decides what it can.
_CURRENCY = {
    "us": "USD", "gb": "GBP", "de": "EUR", "at": "EUR", "ch": "CHF",
    "nl": "EUR", "fr": "EUR", "au": "AUD", "ca": "CAD",
}


def _name(v) -> str:
    """Adzuna nests company/location as objects with a `display_name`."""
    if isinstance(v, dict):
        return str(v.get("display_name") or v.get("name") or "").strip()
    return str(v or "").strip()


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _compensation(j: dict, country: str) -> str:
    lo, hi = _num(j.get("salary_min")), _num(j.get("salary_max"))
    if lo is None and hi is None:
        return ""
    if lo is not None and hi is not None and lo != hi:
        amount = f"{lo:g}-{hi:g}"
    else:
        amount = f"{lo if lo is not None else hi:g}"
    parts = [amount, _CURRENCY.get(country, "")]
    return " ".join(p for p in parts if p)


def parse(data: dict, country: str = "us") -> list[Posting]:
    out = []
    results = data.get("results") if isinstance(data, dict) else None
    for j in results or []:
        if not isinstance(j, dict):
            continue
        loc = j.get("location")
        if not isinstance(loc, dict):
            loc = {}
        cat = j.get("category")
        if not isinstance(cat, dict):
            cat = {}
        out.append(Posting(
            source=NAME,
            title=str(j.get("title") or "").strip(),
            company=_name(j.get("company")),
            location=_name(j.get("location")),
            url=str(j.get("redirect_url") or "").strip(),
            external_id=str(j.get("id") or j.get("redirect_url") or ""),
            posted_at=date_part(str(j.get("created") or "")),
            description=clean_html(str(j.get("description") or ""))[:6000],
            compensation=_compensation(j, country),
            extra={"category": cat.get("label"),
                   "contract_type": j.get("contract_type"),
                   "contract_time": j.get("contract_time"),
                   "salary_is_predicted": j.get("salary_is_predicted"),
                   "area": loc.get("area")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    missing = [k for k in ("app_id", "app_key")
               if not str(settings.get(k) or "").strip()]
    if missing:
        fetcher.errors.append(
            f"adzuna: missing {'/'.join(missing)} — set sources.adzuna.app_id "
            "and sources.adzuna.app_key in the config; free instant key: "
            f"{KEY_URL}"
        )
        return []
    app_id = str(settings["app_id"]).strip()
    app_key = str(settings["app_key"]).strip()
    country = str(settings.get("country") or "us").strip().lower() or "us"
    pages = max(1, int(settings.get("pages", 1) or 1))

    out: list[Posting] = []
    for page in range(1, pages + 1):
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": PAGE_SIZE,
            "content-type": "application/json",
        }
        what = str(settings.get("what") or "").strip()
        where = str(settings.get("where") or "").strip()
        if what:
            params["what"] = what
        if where:
            params["where"] = where
        url = f"{API.format(country=country, page=page)}?{urlencode(params)}"

        try:
            status, text = fetcher.get(url)
        except FetchError as e:
            # FetchError's str() embeds the URL, which carries the app_key —
            # surface only the underlying cause, never the credentials.
            fetcher.errors.append(f"adzuna: network failure: {e.message}")
            break
        except Exception as e:
            fetcher.errors.append(f"adzuna: {type(e).__name__}: {e}")
            break

        if status == 401:
            fetcher.errors.append(
                "adzuna: HTTP 401 AUTH_FAIL — the app_id/app_key were REJECTED. "
                "Check sources.adzuna.app_id and sources.adzuna.app_key "
                f"(free key: {KEY_URL})"
            )
            break
        if status == 400:
            # Observed live: a request with no key answers 400 with an HTML
            # body (only 401 carries the JSON AUTH_FAIL), so 400 here means
            # the key is MISSING or malformed, not rejected.
            fetcher.errors.append(
                "adzuna: HTTP 400 — refused before authentication: the "
                "app_id/app_key are MISSING or malformed. Set "
                "sources.adzuna.app_id and sources.adzuna.app_key "
                f"(free key: {KEY_URL})"
            )
            break
        if status != 200:
            fetcher.errors.append(
                f"adzuna: HTTP {status} from the '{country}' endpoint")
            break

        try:
            data = json.loads(text)
        except ValueError as e:
            fetcher.errors.append(f"adzuna: invalid JSON: {e}")
            break
        batch = parse(data, country)
        out.extend(batch)
        if not batch or len(batch) < PAGE_SIZE:
            break                       # a short page means this was the last
    return out
