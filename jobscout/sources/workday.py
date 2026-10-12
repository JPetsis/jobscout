"""Workday CXS — the per-company job API behind Fortune 500 careers sites.

Verified live 2026-10-12: an unauthenticated POST to NVIDIA's tenant returns
1744 postings and the response carries `"userAuthenticated": false`. Salesforce
answers the same way. There is no key and no registration.

WHY THIS IS WORTH HAVING: it is not a job board, it is the ATS behind a large
share of large US employers, so it reaches companies no aggregator covers
completely. Same shape as greenhouse/lever in this project: a per-company list
in config, fetched per tenant.

CONFIG — reuses the existing `companies` construct, so the token lives where
every other ATS token lives:

    companies:
      workday:
        tokens:
          - nvidia:wd5:NVIDIAExternalCareerSite
          - salesforce:wd12:External_Career_Site

A token is `tenant:wdN:site`, or you can paste the careers URL and it is parsed:

    - https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite

The URL form is accepted because nobody should have to know what "wd5" means to
add their employer; open the careers page and copy the address bar.
"""
from __future__ import annotations

import re

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html

NAME = "workday"
LABEL = "Workday"

# Both host shapes Workday uses in the wild.
_PATH = re.compile(
    r"^https?://([a-z0-9-]+)\.(wd\d+)\.(?:myworkdayjobs|myworkdaysite)\.com"
    r"/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"^([a-z0-9-]+)[:/](wd\d+)[:/](.+)$", re.IGNORECASE)

DEFAULT_PAGES = 3
PAGE_SIZE = 20          # the CXS endpoint's own page size

# The shared Fetcher sends `Accept-Language: de-DE,de;q=0.9,en;q=0.8`, which is
# right for the German boards and wrong here: CXS localises with it, returning
# "2 Standorte" instead of "2 Locations" and "Vor mehr als 30 Tagen" instead of
# "Posted 30+ Days Ago". German location text cannot be matched by the geo
# gate's gazetteer, so the US filter would quietly stop working.
EN_HEADERS = {"Accept-Language": "en-US,en;q=0.9"}


def parse_token(token: str) -> tuple[str, str, str] | None:
    """`nvidia:wd5:Site` or a careers URL -> (tenant, 'wd5', site)."""
    token = (token or "").strip()
    if not token:
        return None
    m = _PATH.match(token)
    if m:
        return m.group(1).lower(), m.group(2).lower(), m.group(3)
    m = _TOKEN.match(token)
    if m:
        return m.group(1).lower(), m.group(2).lower(), m.group(3)
    return None


def parse(data: dict, tenant: str, wd: str, site: str) -> list[Posting]:
    out = []
    for j in data.get("jobPostings") or []:
        path = str(j.get("externalPath") or "")
        bullets = j.get("bulletFields") or []
        out.append(Posting(
            source=NAME,
            title=j.get("title", ""),
            company=tenant,
            location=str(j.get("locationsText") or ""),
            url=(f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}{path}"
                 if path else ""),
            external_id=str(bullets[0]) if bullets else path,
            # postedOn is a phrase ("Posted 30+ Days Ago"), not a date, so it is
            # deliberately NOT mapped to posted_at: date_part would either drop
            # it or invent a date. It goes to extra for display instead.
            posted_at="",
            extra={"tenant": tenant, "site": site, "posted_text": j.get("postedOn", "")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    tokens = list(settings.get("companies") or [])
    if not tokens:
        # Not an error: the source is enabled but no employer was added yet.
        # Enabling a source with nothing configured is a normal state, so this
        # stays quiet while a BAD token (below) is reported.
        return []

    queries = settings.get("queries") or [{}]
    pages = int(settings.get("pages") or DEFAULT_PAGES)
    out: list[Posting] = []

    for token in tokens:
        parts = parse_token(str(token))
        if parts is None:
            fetcher.errors.append(
                f"{NAME}: cannot read token {token!r} — expected "
                "'tenant:wdN:Site' or a myworkdayjobs.com careers URL")
            continue
        tenant, wd, site = parts
        url = (f"https://{tenant}.{wd}.myworkdayjobs.com"
               f"/wday/cxs/{tenant}/{site}/jobs")

        for q in queries:
            search = str(q.get("keywords") or "").strip()
            for page in range(pages):
                payload = {"appliedFacets": {}, "limit": PAGE_SIZE,
                           "offset": page * PAGE_SIZE, "searchText": search}
                try:
                    data = fetcher.post_json(url, payload, headers=EN_HEADERS)
                except Exception as e:
                    fetcher.errors.append(f"{NAME}/{tenant}: {e}")
                    break
                batch = parse(data, tenant, wd, site)
                out.extend(batch)
                total = int(data.get("total") or 0)
                if not batch or (page + 1) * PAGE_SIZE >= total:
                    break
    return out


def fetch_detail(posting: Posting, fetcher: Fetcher) -> str | None:
    """The CXS job-detail endpoint returns the full description.

    Present so `jobscout show` can expand a Workday posting; the list endpoint
    does not include the body.
    """
    tenant = (posting.extra or {}).get("tenant")
    site = (posting.extra or {}).get("site")
    if not tenant or not site or not posting.url:
        return None
    # externalPath is the tail of the posting URL
    path = posting.url.split(f"/{site}", 1)[-1]
    if not path.startswith("/"):
        return None
    detail_url = (f"https://{tenant}.wd1.myworkdayjobs.com"
                  f"/wday/cxs/{tenant}/{site}{path}")
    try:
        data = fetcher.get_json(detail_url)
    except Exception:                                          # noqa: BLE001
        return None
    info = (data.get("jobPostingInfo") or {})
    body = info.get("jobDescription") or ""
    return clean_html(body)[:8000] if body else None
