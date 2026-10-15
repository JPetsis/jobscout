"""academics.de -- Germany's academic jobs board (ZEIT group).

Also carries universities and research institutes in Austria and
Switzerland, and covers every discipline (W1-W3 professorships, postdocs,
wissenschaftliche Mitarbeiter, science management, PhD positions).

`GET /stellenanzeigen?page=N` is a server-rendered listing, 50 cards per
page, newest first. The site's keyword input is IGNORED on this URL:
`?search_module[search_input]=physik` returned the same 50 ids minus one in a
live probe (2026-10-15), so this adapter walks the newest pages instead of
querying. Verified live: `?page=2`..`11` page correctly.

Card markup: `<li id="job-teaser-<id>-container">`; the anchor `#job-<id>`
carries `href="/jobs/<slug>-<id>"` and the full title. The employer sits in
the logo image's `alt="<company> - Logo"`, the city in the `truncate` span
inside the `bg-primary-transparency-90` badge (Vollzeit/Befristet use
neutral badges, which is what distinguishes location from contract text),
and the publish date as dd.mm.yyyy. Detail pages are server-rendered; the
generic HTML extractor handles them.
"""
from __future__ import annotations

import re
from html import unescape

from ..http import Fetcher
from ..models import Posting

NAME = "academics"
LABEL = "academics.de"
API = "https://www.academics.de/stellenanzeigen"

_CARD = re.compile(r'<li id="job-teaser-(\d+)-container">')
_LINK = re.compile(r'<a id="job-(\d+)"[^>]*href="(/jobs/[^"]+)"[^>]*title="([^"]*)"')
_H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
_COMPANY = re.compile(r'alt="([^"]+?) - Logo"')
_CITY = re.compile(r'bg-primary-transparency[^"]*"[^>]*>\s*<span class="truncate">\s*([^<]+?)\s*</span>')
_DATE = re.compile(r'<span class="truncate">\s*(\d{2})\.(\d{2})\.(\d{4})\s*</span>')
_BADGE = re.compile(r'<span class="truncate">\s*([^<]+?)\s*</span>')
_CONTRACT = re.compile(r"^(unbefristet|befristet)$", re.I)
_WORKTIME = re.compile(r"^(vollzeit|teilzeit|vollzeit/teilzeit)$", re.I)


def parse(text: str) -> list[Posting]:
    text = text or ""
    starts = [m.start() for m in _CARD.finditer(text)]
    out: list[Posting] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        chunk = text[start:end]
        link = _LINK.search(chunk)
        if not link:
            continue
        jid, href = link.group(1), link.group(2)
        title = unescape(link.group(3)).strip()
        if not title:
            h2 = _H2.search(chunk)
            title = unescape(re.sub(r"<[^>]+>", "", h2.group(1))).strip() if h2 else ""
        if not title:
            continue
        company = _COMPANY.search(chunk)
        city = _CITY.search(chunk)
        date = _DATE.search(chunk)
        # The verdict badges feed scoring: "Befristung: Unbefristet" in the
        # description is what the permanence boost looks for.
        badges = [b.strip() for b in _BADGE.findall(chunk)]
        contract = next((b for b in badges if _CONTRACT.match(b)), "")
        worktime = next((b for b in badges if _WORKTIME.match(b)), "")
        note = " · ".join(x for x in (
            (f"Arbeitszeit: {worktime}" if worktime else ""),
            (f"Befristung: {contract}" if contract else "")) if x)
        out.append(Posting(
            source=NAME,
            title=title,
            company=(unescape(company.group(1)).strip() if company else ""),
            location=(unescape(city.group(1)).strip() if city else ""),
            url="https://www.academics.de" + href,
            external_id=jid,
            posted_at=(f"{date.group(3)}-{date.group(2)}-{date.group(1)}" if date else ""),
            description=note,
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for page in range(1, int(settings.get("pages", 2)) + 1):
        url = API if page == 1 else f"{API}?page={page}"
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
