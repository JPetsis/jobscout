"""The Muse — public jobs API, no key required.

Verified live 2026-10-12: GET with no auth returns 860 US "Software
Engineering" postings, structured locations and a real description.

Two things about this API shape the adapter:

1. There is NO free-text search parameter. The usable axes are `category`,
   `location` and `level`, so this source cannot use the profile-derived queries
   the way the search-based boards do. It fetches by category (or everything)
   and lets `score.py` decide relevance, which is the same fetch-broad /
   filter-later split the rest of the pipeline uses.

2. A key is NOT required. The docs say registration is required "for any use
   beyond testing" and raises the rate limit, so a key is supported (`api_key`)
   but optional, and its absence is not an error.
"""
from __future__ import annotations

from urllib.parse import urlencode

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, date_part

NAME = "themuse"
LABEL = "The Muse"
API = "https://www.themuse.com/api/public/jobs"

# The API pages at 20 and reports page_count, so a hard ceiling is the only
# thing between us and thousands of requests. 5 pages = 100 postings.
DEFAULT_PAGES = 5


def parse(data: dict) -> list[Posting]:
    out = []
    for j in data.get("results") or []:
        refs = j.get("refs") or {}
        company = (j.get("company") or {}).get("name") or ""
        locs = [l.get("name", "") for l in (j.get("locations") or []) if l.get("name")]
        cats = [c.get("name", "") for c in (j.get("categories") or []) if c.get("name")]
        levels = [l.get("name", "") for l in (j.get("levels") or []) if l.get("name")]
        out.append(Posting(
            source=NAME,
            title=j.get("name", ""),
            company=company,
            location="; ".join(locs),
            url=refs.get("landing_page", ""),
            external_id=str(j.get("id", "")),
            posted_at=date_part(str(j.get("publication_date") or "")),
            description=clean_html(j.get("contents", ""))[:8000],
            extra={"categories": cats, "levels": levels},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    pages = int(settings.get("pages") or DEFAULT_PAGES)
    categories = [str(c).strip() for c in (settings.get("categories") or []) if str(c).strip()]
    location = str(settings.get("location") or "").strip()
    api_key = str(settings.get("api_key") or "").strip()

    # No categories configured means "everything the API will give us", newest
    # first. That keeps the source useful for a profile whose field has no
    # matching category, instead of silently returning nothing.
    category_list = categories or [""]

    out: list[Posting] = []
    for category in category_list:
        for page in range(1, pages + 1):
            params: dict = {"page": page, "descending": "true"}
            if category:
                params["category"] = category
            if location:
                params["location"] = location
            if api_key:
                params["api_key"] = api_key
            try:
                data = fetcher.get_json(f"{API}?{urlencode(params)}")
            except Exception as e:  # one dead page must not kill the source
                fetcher.errors.append(f"{NAME}/{category or 'all'} p{page}: {e}")
                break
            batch = parse(data)
            out.extend(batch)
            if not batch or page >= int(data.get("page_count") or 0):
                break
    return out
