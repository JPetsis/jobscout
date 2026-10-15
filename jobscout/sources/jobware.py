"""Jobware — German job board, server-rendered (Qwik).

Search: `GET /jobs?q=<keywords>&page=N` (the canonical form the site itself
uses is `/jobs--fuer-<kw>`). THE TRAP: result cards are BUTTONS, not links —
each carries `data-job-detail-url="/job/<slug>-<id>"` and there is no plain
anchor to scrape, so href-based parsing sees nothing at all. Qwik also leaves
HTML comments inside text nodes (`<!--t=x-->`), so comments are stripped
before parsing (otherwise every field regex needs comment-aware gymnastics).

Card fields: `<h2>` title, `job-card__advertiser-name` company, a
visually-hidden "in" followed by `<City> (DE|AT|CH)`, and a `<time datetime>`
publish date. Verified live 2026-10-15: q=php returns real PHP roles and
page 2 returns different rows. No ld+json, no salary on the card, and the
site's location filter has no verified parameter name, so only `q` and `page`
are sent.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from ..http import Fetcher
from ..models import Posting
from ..textutil import date_part

NAME = "jobware"
LABEL = "Jobware"
API = "https://www.jobware.de/jobs"

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_H2 = re.compile(r"<h2[^>]*>(?P<title>[^<]+)</h2>")
_COMPANY = re.compile(r'job-card__advertiser-name">\s*([^<]+?)\s*<')
_LOCATION = re.compile(r'nrmdy-visually-hidden">in</span>\s*([^<]+?)\s*<')
_DETAIL = re.compile(r'data-job-detail-url="(/job/[^"]+)"')
_TIME = re.compile(r'<time dateTime="([^"]+)"')


def parse(text: str) -> list[Posting]:
    text = _COMMENT.sub("", text or "")
    h2s = list(_H2.finditer(text))
    out = []
    for i, m in enumerate(h2s):
        start = m.start()
        end = h2s[i + 1].start() if i + 1 < len(h2s) else len(text)
        chunk = text[start:end]
        det = _DETAIL.search(chunk)
        title = m.group("title").strip()
        if not det or not title:
            continue
        company = _COMPANY.search(chunk)
        loc = _LOCATION.search(chunk)
        tm = _TIME.search(chunk)
        ext = re.search(r"-(\d{6,})$", det.group(1))
        out.append(Posting(
            source=NAME,
            title=title,
            company=(company.group(1).strip() if company else ""),
            location=(loc.group(1).strip() if loc else ""),
            url="https://www.jobware.de" + det.group(1),
            external_id=(ext.group(1) if ext else det.group(1)),
            posted_at=(date_part(tm.group(1)) if tm else ""),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        if q.get("location"):
            fetcher.errors.append(
                f"{NAME}: location filter not supported (no verified param); "
                f"ignoring {q.get('location')!r}")
        kw = str(q.get("keywords") or "")
        for page in range(1, int(q.get("pages", 1)) + 1):
            url = f"{API}?q={quote_plus(kw)}"
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
