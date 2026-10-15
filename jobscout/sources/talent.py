"""talent.com — global job aggregator with a German front (de.talent.com).

`GET https://de.talent.com/jobs?k=<kw>&l=<loc>&p=<page>` is server-rendered:
20 cards per page (verified live 2026-10-15, `&p=2` returns different rows).
Cards are `div[data-testid="jobcard-container-<id>"]` and carry
`data-new-id` (the /view?id= detail), an `h2.JobCard_title`, a
`span.JobCard_company`, a `span.JobCard_location`, and a `<time datetime=...>`
("Zuletzt aktualisiert" = last updated). The page also ships an ItemList
JSON-LD, but the card markup carries more fields, so the cards are parsed.

Locations come as "Berlin, Deutschland" and survive the geo gate.
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote_plus

from ..http import Fetcher
from ..models import Posting
from ..textutil import date_part

NAME = "talent"
LABEL = "Talent.com"
API = "https://de.talent.com/jobs"

_CARD = 'data-testid="jobcard-container-'
_TITLE = re.compile(r'class="JobCard_title[^"]*">([^<]+)<')
_COMPANY = re.compile(r'class="JobCard_company[^"]*">([^<]+)<')
_LOCATION = re.compile(r'class="JobCard_location[^"]*">([^<]+)<')
_TIME = re.compile(r'<time[^>]*dateTime="([^"]+)"')


def parse(text: str) -> list[Posting]:
    out = []
    for chunk in (text or "").split(_CARD)[1:]:
        m_id = re.match(r"(\d+)", chunk)
        t = _TITLE.search(chunk)
        if not m_id or not t:
            continue
        jid = m_id.group(1)
        comp = _COMPANY.search(chunk)
        loc = _LOCATION.search(chunk)
        tm = _TIME.search(chunk)
        out.append(Posting(
            source=NAME,
            title=unescape(t.group(1)).strip(),
            company=(unescape(comp.group(1)).strip() if comp else ""),
            location=(unescape(loc.group(1)).strip() if loc else ""),
            url=f"https://de.talent.com/view?id={jid}",
            external_id=jid,
            posted_at=(date_part(tm.group(1)) if tm else ""),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        kw = quote_plus(str(q.get("keywords") or ""))
        loc = quote_plus(str(q.get("location") or ""))
        for page in range(1, int(q.get("pages", 1)) + 1):
            url = f"{API}?k={kw}&l={loc}"
            if page > 1:
                url += f"&p={page}"
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
