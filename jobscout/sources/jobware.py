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

import html
import re
from urllib.parse import quote_plus

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, date_part

NAME = "jobware"
LABEL = "Jobware"
API = "https://www.jobware.de/jobs"

_COMMENT = re.compile(r"<!--.*?-->", re.S)
# The card is a Qwik component whose markup moved: the detail link is now a
# plain anchor in the footer (`data-job-detail-link`), next to a real
# `<time dateTime=…>`, and the title sits in `job-card__title-wrapper`. The old
# parser looked for `data-job-detail-url` on a BUTTON that no longer exists, so
# it stored nothing for eight runs while the page answered 200.
_CARD_SPLIT = 'class="job-card__title-wrapper"'
_H2 = re.compile(r"<h2[^>]*>(?P<title>.*?)</h2>", re.S)
_COMPANY = re.compile(r'job-card__advertiser-name[^"]*"[^>]*>\s*([^<]+?)\s*<')
# The place sits AFTER a visually-hidden "in" span inside the location tag
# ("…<span class=visually-hidden>in</span> Berlin"), so capturing the tag's
# text yields "in". The old pattern was right about that; it is kept.
_LOCATION = re.compile(r'nrmdy-visually-hidden">in</span>\s*([^<]+?)\s*<')
_DETAIL = re.compile(r'href="(/job/[^"]+)"')
_TIME = re.compile(r'<time[^>]*dateTime="([^"]+)"')


def parse(text: str) -> list[Posting]:
    text = _COMMENT.sub("", text or "")
    chunks = text.split(_CARD_SPLIT)[1:]
    out = []
    for chunk in chunks:
        m = _H2.search(chunk)
        det = _DETAIL.search(chunk)
        title = clean_html(m.group("title")) if m else ""
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
            location=clean_html(loc.group(1)) if loc else "",
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


def _body(page: str) -> str:
    """The advert's text, which Jobware ships inside a script as escaped HTML.

    The detail page is a 565 KB client-rendered document with no JSON-LD body and
    no Next/Nuxt payload, so html_detail found nothing and every Jobware card was
    a title and a link. The advert is one long escaped string in the page's own
    data, with <br> for line breaks; it is cut out at the first section heading
    and unescaped.
    """
    for marker in ("Deine Aufgaben", "Ihre Aufgaben", "Stellenbeschreibung",
                   "Aufgabenbereich"):
        i = page.find(marker)
        if i == -1:
            continue
        start, end = page.rfind('"', 0, i), page.find('"', i)
        if start == -1 or end == -1 or end - start < 400:
            continue
        text = page[start + 1:end]
        text = text.replace("\\n", "\n").replace('\\"', '"').replace("\\u00e4", "ä")
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        lines = [" ".join(l.split()) for l in text.splitlines()]
        out = "\n".join(l for l in lines if l)
        if len(out) > 400:
            # The facts line ("Beschäftigungsart ... Befristungsart Befristet")
            # sits above the body and is the only place a fixed-term contract is
            # stated; without it the colloquial "Festanstellung" in the opening
            # sentence was all the scorer ever saw.
            facts = re.search(r"Beschäftigungsart[^\"<]{0,220}", page)
            if facts:
                line = html.unescape(re.sub(r"<[^>]+>", " ", facts.group(0)))
                out = " ".join(line.split()) + "\n" + out
            return out
    return ""


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    try:
        status, page = fetcher.get(p.url)
    except Exception:                                    # noqa: BLE001
        return None
    if status != 200:
        return None
    return _body(page) or None
