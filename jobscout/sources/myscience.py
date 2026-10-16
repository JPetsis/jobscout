"""myScience -- science jobs in Germany, Switzerland and Austria.

Search: `GET /jobs?q=<keywords>` (the site's own search target, verified
live 2026-10-15; `q=professur` returned 21 items where the unfiltered page
returns 13). The loader is ISO-8859-15, which is why the polite Fetcher now
honours the declared charset -- decoding those pages as UTF-8 mangled every
umlaut into \ufffd.

Only the first page (21 items) is fetched per query: a second page exists
via `&s=NN`, but neither size nor offset semantics could be confirmed
against the listing's churn (probes minutes apart disagreed about where an
offset landed), so this adapter deliberately does not bet on a guessed
parameter. `location` in a query dict is NOT supported -- the form takes one
combined string -- and is reported loudly instead of dropped.

Items are schema.org ListItems: link `/jobs/id<7>-<slug>`, title
`itemprop="name"`, organisation `results_organization`, place
`span.location`, publish date `<time datetime>`, teaser `span.abs`.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html

NAME = "myscience"
LABEL = "myScience"
API = "https://www.myscience.de/jobs"

_ITEM = re.compile(r'<div itemprop="itemListElement"')
_HREF = re.compile(r'href="(/jobs/id\d+[^"]*)"')
_TITLE = re.compile(r'<span itemprop="name">(.*?)</span>', re.S)
_LOCATION = re.compile(r'<span class="location">([^<]+)</span>')
_DATE = re.compile(r'<time datetime="([^"]+)"')
_ORG = re.compile(r'<span class="results_organization">([^<]+)</span>')
_DESC = re.compile(r'<span class="abs" itemprop="description">(.*?)</span>', re.S)
# The per-job page publishes the advert as HTML inside the body's
# `itemprop="description"` region. The generic extractor only finds the
# og/meta blurb (~330 chars), which hides most of the text -- measured on
# the Leipzig DevOps row, whose rescore lost its PHP mention because the
# blurb does not name the stack.
_BODY = re.compile(r'itemprop="description"[^>]*>')


def parse(text: str) -> list[Posting]:
    text = text or ""
    starts = [m.start() for m in _ITEM.finditer(text)]
    out: list[Posting] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        chunk = text[start:end]
        href = _HREF.search(chunk)
        t = _TITLE.search(chunk)
        title = clean_html(t.group(1)) if t else ""
        if not href or not title:
            continue
        loc = _LOCATION.search(chunk)
        date = _DATE.search(chunk)
        org = _ORG.search(chunk)
        desc = _DESC.search(chunk)
        out.append(Posting(
            source=NAME,
            title=title,
            company=(clean_html(org.group(1)) if org else ""),
            location=(clean_html(loc.group(1)) if loc else ""),
            url="https://www.myscience.de" + href.group(1),
            external_id=(re.search(r"id(\d+)", href.group(1)) or [None, href.group(1)])[1],
            posted_at=(date.group(1)[:10] if date else ""),
            description=(clean_html(desc.group(1))[:1500] if desc else ""),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        if q.get("location"):
            fetcher.errors.append(
                f"{NAME}: location filter not supported (one combined search "
                f"string only); ignoring {q.get('location')!r}")
        kw = str(q.get("keywords") or "")
        url = f"{API}?q={quote_plus(kw)}" if kw else API
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
    try:
        status, text = fetcher.get(p.url)
    except Exception:
        status, text = 0, ""
    if status == 200 and text:
        m = _BODY.search(text or "")
        if m:
            end = text.find("<footer", m.end())
            body = text[m.end(): end if end > 0 else len(text)]
            desc = clean_html(body)
            if len(desc) > 600:
                return desc[:6000]
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
