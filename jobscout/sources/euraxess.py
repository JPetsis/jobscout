"""EURAXESS -- the EU's official researcher-jobs portal (all disciplines).

`GET /jobs/search` is server-rendered, 10 offers per page, newest first.
Pagination is ZERO-based on the site: the bare URL is the first page and
`?page=1` is the SECOND (verified 2026-10-15: bare, page=1 and page=2 are
three disjoint 10-id sets). The search form's keyword input
(`name="keywords"`) is IGNORED over GET -- keywords/keyword/q/text/search/
title all returned the identical set -- so this adapter walks the newest
pages instead of querying.

Items are `article.ecl-content-item` blocks: organisation link
(`/partnering/organisations/profile/...`), `Posted on: 15 October 2026`,
title link `/jobs/<id>`, and a description paragraph. Cards carry no
location (it lives in the detail page's "Work Location(s)" section); detail
pages are server-rendered, so fetch_detail uses the generic extractor.
"""
from __future__ import annotations

import datetime as _dt
import re

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html

NAME = "euraxess"
LABEL = "EURAXESS (EU)"
API = "https://euraxess.ec.europa.eu/jobs/search"

_ARTICLE = re.compile(r'<article class="ecl-content-item">')
_ORG = re.compile(r'href="/partnering/organisations/profile/[^"]*"[^>]*>\s*([^<]+?)\s*</a>')
_DATE = re.compile(r"Posted on:\s*([0-9]{1,2} [A-Za-z]+ [0-9]{4})")
_TITLE = re.compile(r'<h3 class="ecl-content-block__title">\s*<a\s+href="/jobs/(\d+)"[^>]*>\s*<span>(.*?)</span>', re.S)
_DESC = re.compile(r'<div class="ecl-content-block__description">(.*?)</div>', re.S)


def _posted(raw: str) -> str:
    try:
        return _dt.datetime.strptime(raw.strip(), "%d %B %Y").date().isoformat()
    except ValueError:
        return ""


def parse(text: str) -> list[Posting]:
    text = text or ""
    starts = [m.start() for m in _ARTICLE.finditer(text)]
    out: list[Posting] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        chunk = text[start:end]
        t = _TITLE.search(chunk)
        if not t:
            continue
        jid, title = t.group(1), clean_html(t.group(2))
        if not title:
            continue
        org = _ORG.search(chunk)
        date = _DATE.search(chunk)
        desc = _DESC.search(chunk)
        out.append(Posting(
            source=NAME,
            title=title,
            company=(clean_html(org.group(1)) if org else ""),
            location="",                       # only on the detail page
            url=f"https://euraxess.ec.europa.eu/jobs/{jid}",
            external_id=jid,
            posted_at=(_posted(date.group(1)) if date else ""),
            description=(clean_html(desc.group(1))[:1500] if desc else ""),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for page in range(1, int(settings.get("pages", 2)) + 1):
        url = API if page == 1 else f"{API}?page={page - 1}"
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
