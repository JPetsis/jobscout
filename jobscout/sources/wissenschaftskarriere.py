"""Wissenschaftskarriere (DUZ) -- academic jobs portal (DE/AT/CH).

`GET /suchergebnis.html` returns the newest 20 offers, server-rendered.
Plain HTTP reaches no more: page/seite/_p/p all returned the IDENTICAL set
in live probes (2026-10-15), and the page's own counter marks the rest as
script-loaded (class `jwtpl-hili-itemAjaxCount`). The site's search is a
form POST target (`/suchergebnis.html`), and `?q=`/`?search=`/`?keywords=`/
`?suchbegriff=` are all ignored over GET, so this adapter takes the newest
page as-is and does not pretend to query.

Items are `<a class="jwtpl-hili-itemLink" href=".../job/<slug>.<id>.html">`
blocks carrying: title (`jwtpl-hili-itemTitel`), date (`jwtpl-hili-itemDate`,
dd.mm.yyyy or a relative phrase), company (`jwtpl-hili-itemCompany`) and
place (`jwtpl-hili-itemLocation`). The span classes are matched with the
leading `<span class="` so the wrapper divs' `...DateMarkup7d` hook classes
cannot be mistaken for the date field.
"""
from __future__ import annotations

import re

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, de_rel_date

NAME = "wissenschaftskarriere"
LABEL = "DUZ Wissenschaftskarriere"
API = "https://wissenschaftskarriere.duz.de/suchergebnis.html"

_ITEM = re.compile(r'<a class="jwtpl-hili-itemLink" href="([^"]+?)"')
_TITLE = re.compile(r'<span class="jwtpl-hili-itemTitel"[^>]*>(.*?)</span>', re.S)
_DATE = re.compile(r'<span class="jwtpl-hili-itemDate"[^>]*>(.*?)</span>', re.S)
_COMPANY = re.compile(r'<span[^>]*class="jwtpl-hili-itemCompany[^"]*"[^>]*>(.*?)</span>', re.S)
_LOCATION = re.compile(r'<span class="jwtpl-hili-itemLocation"[^>]*>(.*?)</span>', re.S)
_DE_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_EXT = re.compile(r"\.(\d{8,})\.html$")


def _posted(raw: str) -> str:
    raw = clean_html(raw)
    m = _DE_DATE.search(raw)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return de_rel_date(raw)


def parse(text: str) -> list[Posting]:
    text = text or ""
    starts = [m.start() for m in _ITEM.finditer(text)]
    out: list[Posting] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        chunk = text[start:end]
        href = _ITEM.search(chunk)
        title_m = _TITLE.search(chunk)
        title = clean_html(title_m.group(1)) if title_m else ""
        if not href or not title:
            continue
        company = _COMPANY.search(chunk)
        loc = _LOCATION.search(chunk)
        date = _DATE.search(chunk)
        ext = _EXT.search(href.group(1))
        out.append(Posting(
            source=NAME,
            title=title,
            company=(clean_html(company.group(1)) if company else ""),
            location=(clean_html(loc.group(1)) if loc else ""),
            url=href.group(1),
            external_id=(ext.group(1) if ext else href.group(1)),
            posted_at=(_posted(date.group(1)) if date else ""),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    try:
        status, text = fetcher.get(API)
    except Exception as e:
        fetcher.errors.append(f"{NAME}: {e}")
        return []
    if status != 200:
        fetcher.errors.append(f"{NAME}: HTTP {status}")
        return []
    return parse(text)


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
