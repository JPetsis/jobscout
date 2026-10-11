"""AMS eJob-Room (jobs.ams.at) — the Austrian public employment service.

The portal is an Angular SPA whose JSON API is reachable without any account
or API key. Each request must carry three headers that the app derives
client-side:

    x-ams-access-token         = HMAC-SHA512(key, message), hex
    x-ams-access-token-random  = 8 random bytes, hex
    x-ams-access-token-timestamp = unix seconds

where the message is, with no separators:

    "GET" + <path> + <query string> +
    "X-AMS-ACCESS-TOKEN-RANDOM=" + random + "&" +
    "X-AMS-ACCESS-TOKEN-TIMESTAMP=" + timestamp

The query string in the message is the **alphabetically sorted** parameter
string (the app normalises params before signing), and `key` is a 64-character
constant shipped in the public app bundle — no secret, no login, and the server
rejects nothing else. Verification: replaying a signature captured from the
live site reproduces it byte for byte, and plain HTTP from Python returns 200.

The algorithm is SHA-512, not SHA-256 (a 128-hex-char signature is the tell).

Search: GET /public/emps/api/search?query=&page=&pageSize=&sortField=&sortOrder=
`pageSize` maxes at 100 (200 answers HTTP 400). Results carry a full HTML
`summary`, the employer, the Austrian federal state and `lastUpdatedAt`.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse

from ..http import Fetcher, FetchError
from ..models import Posting
from ..textutil import clean_html, date_part

NAME = "ams"
LABEL = "AMS eJob-Room"
API = "https://jobs.ams.at/public/emps/api/search"
# The detail page is keyed by the offer's **uuid**, not its numeric id. The old
# `/public/emps/jobDetails?jobId=<id>` route is gone: the current Angular bundle
# registers only `jobs` and `saved-jobs`, and every jobDetails URL falls through
# to a CMS shell whose skip-links render as the literal word "Accesskey" above
# "Ein unbekannter Fehler ist aufgetreten". Verified against the live app: the
# real detail links are `/public/emps/jobs/<uuid>` and they render correctly.
PORTAL = "https://jobs.ams.at/public/emps/jobs/{uuid}"

# Shipped in the public app bundle (see module docstring). Not a credential:
# every anonymous visitor downloads it, and the API needs no login.
SIGNING_KEY = "wh5tutirqnko3a2a3h9m2bbt3sfvth6q6rnvjruc1hiumjw9ptcqofbme3kuktwb"


def _query(params: dict) -> str:
    """Sorted query string for the URL, with spaces as ``+``."""
    return "&".join(f"{k}={urllib.parse.quote_plus(str(v))}"
                    for k, v in sorted(params.items()) if v not in (None, ""))


def _sign_query(params: dict) -> str:
    """The signed form: sorted keys, values **raw** (NOT url-encoded).

    The server reconstructs the message from the decoded parameter values, so
    signing an encoded string (``query=a%20b``) yields HTTP 401 while signing
    ``query=a b`` is accepted. Percent-encoding the signature is the one thing
    that must not be done here.
    """
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v not in (None, ""))


def _token(path: str, params: dict) -> dict[str, str]:
    """The three auth headers for one GET."""
    ts = str(int(time.time()))
    rand = secrets.token_hex(8)
    message = (f"GET{path}{_sign_query(params)}"
               f"X-AMS-ACCESS-TOKEN-RANDOM={rand}"
               f"&X-AMS-ACCESS-TOKEN-TIMESTAMP={ts}")
    sig = hmac.new(SIGNING_KEY.encode(), message.encode(), hashlib.sha512).hexdigest()
    return {
        "x-ams-access-token": sig,
        "x-ams-access-token-random": rand,
        "x-ams-access-token-timestamp": ts,
    }


def _location(offer: dict) -> str:
    loc = (offer.get("workingLocation")
           or (offer.get("company") or {}).get("address")
           or {})
    parts = [loc.get("town"), loc.get("federalState"), loc.get("countryCode")]
    return ", ".join(p for p in parts if p)


def parse(data: dict) -> list[Posting]:
    out = []
    for o in data.get("results") or []:
        if not isinstance(o, dict):
            continue
        oid = o.get("id")
        title = (o.get("title") or "").strip()
        if not oid or not title:
            continue
        company = o.get("company") if isinstance(o.get("company"), dict) else {}
        # `uuid` is the detail-page key. Without it there is no openable URL, so
        # storing the row would only produce a dead link in the digest.
        uuid = (o.get("uuid") or "").strip()
        if not uuid:
            continue
        out.append(Posting(
            source=NAME,
            title=title,
            company=(company.get("name") or "").strip(),
            location=_location(o),
            url=PORTAL.format(uuid=urllib.parse.quote(uuid, safe="")),
            external_id=str(oid),
            posted_at=date_part(str(o.get("lastUpdatedAt") or "")),
            description=clean_html(o.get("summary") or "")[:8000],
            extra={"uuid": uuid,
                   "green": bool(o.get("isGreenJob")),
                   "preselection": bool(o.get("isPreselection")),
                   "employment": (o.get("employmentRelationship") or {}).get("description", ""),
                   "workingTime": (o.get("workingTime") or {}).get("description", "")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    queries = settings.get("queries") or [
        {"keywords": "Softwareentwickler"},
        {"keywords": "Softwareentwickler_in"},
        {"keywords": "Software Developer"},
        {"keywords": "DevOps"},
    ]
    max_pages = int(settings.get("max_pages", 2))
    page_size = min(int(settings.get("page_size", 100)), 100)   # 200 -> HTTP 400
    sort_field = settings.get("sort_field", "_LAST_MODIFIED")
    sort_order = settings.get("sort_order", "desc")
    max_age_days = int(settings.get("max_age_days", 45))
    cutoff = (_dt.date.today() - _dt.timedelta(days=max_age_days)).isoformat()

    out: list[Posting] = []
    seen: set[str] = set()
    for q in queries:
        if not isinstance(q, dict):
            continue
        keyword = (q.get("keywords") or "").strip()
        if not keyword:
            continue
        for page in range(1, max_pages + 1):
            params = {"query": keyword, "page": page, "pageSize": page_size,
                      "sortField": sort_field, "sortOrder": sort_order}
            extra_params = q.get("params")
            if isinstance(extra_params, dict):
                for extra, value in extra_params.items():
                    params[extra] = value
            query = _query(params)
            path = "/public/emps/api/search"
            try:
                status, text = fetcher.get(f"{API}?{query}",
                                           headers=_token(path, params))
            except FetchError as e:
                fetcher.errors.append(f"ams/{keyword}: {e}")
                break
            if status != 200:
                fetcher.errors.append(f"ams/{keyword}: HTTP {status}")
                break
            try:
                data = json.loads(text)
            except ValueError as e:
                fetcher.errors.append(f"ams/{keyword}: invalid JSON: {e}")
                break
            batch = parse(data)
            if not batch:
                break
            for p in batch:
                if p.external_id not in seen:
                    seen.add(p.external_id)
                    out.append(p)
            if min((p.posted_at or "9999") for p in batch) < cutoff:
                break                   # walked past the age window
            if len(batch) < page_size:
                break
    return out
