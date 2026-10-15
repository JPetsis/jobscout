"""GovernmentJobs.com (NEOGOV) — US public-sector job board, server-rendered.

`GET /jobs?keyword=<kw>` returns 20 cards per page (verified live 2026-10-15);
`&page=N` works (page 2 returns different rows). Each card links
`/jobs/<id>-<n>/<slug>` and carries the company in `job-organization`, the
location in `job-location`, and a pay line ("Full Time | $91,083.20 -
$116,230.40 Annually | Closes in 2 weeks") that goes to `compensation`.

An empty keyword returns a small featured sweep, which is what the generated
`broad` query relies on. The site also has a location filter parameter, but
its exact name was not verified, so only keyword/page are sent.
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote_plus

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html

NAME = "governmentjobs"
LABEL = "GovernmentJobs (US)"
API = "https://www.governmentjobs.com/jobs"

_ANCHOR = re.compile(
    r'<a[^>]*class="job-details-link"[^>]*href="(/jobs/[^"]+)"[^>]*>([^<]+)</a>')
_COMPANY = re.compile(r'class="[^"]*job-organization"[^>]*>([^<]+)<')
_LOCATION = re.compile(r'class="[^"]*job-location"[^>]*>([^<]+)<')
_PAY = re.compile(r'class="primaryInfo">\s*([^<]*[|$][^<]*?)\s*<')


def parse(text: str) -> list[Posting]:
    matches = list(_ANCHOR.finditer(text or ""))
    out = []
    for i, m in enumerate(matches):
        start = m.start()
        end = (matches[i + 1].start() if i + 1 < len(matches)
               else min(len(text), start + 6000))
        chunk = text[start:end]
        comp = _COMPANY.search(chunk)
        loc = _LOCATION.search(chunk)
        pay = _PAY.search(chunk)
        path = m.group(1)
        parts = [p for p in path.split("/") if p]
        out.append(Posting(
            source=NAME,
            title=unescape(m.group(2)).strip(),
            company=(unescape(comp.group(1)).strip() if comp else ""),
            location=(unescape(loc.group(1)).strip() if loc else ""),
            url="https://www.governmentjobs.com" + unescape(path),
            external_id=parts[1] if len(parts) > 1 else path,
            compensation=(clean_html(pay.group(1)) if pay else ""),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        if q.get("location"):
            fetcher.errors.append(
                f"{NAME}: location filter not supported (no verified param); "
                f"ignoring {q.get('location')!r}")
        kw = quote_plus(str(q.get("keywords") or ""))
        for page in range(1, int(q.get("pages", 1)) + 1):
            url = f"{API}?keyword={kw}"
            if page > 1:
                url += f"&page={page}"
            try:
                status, text = fetcher.get(url)
            except Exception as e:
                fetcher.errors.append(f"{NAME}: {e}")
                break
            if status != 200:
                fetcher.errors.append(f"{NAME}: HTTP {status}")
                break
            out.extend(parse(text))
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
