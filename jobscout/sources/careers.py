"""Employers' own careers pages, for firms that run no job board at all.

The watchlist used to accept only Greenhouse, Ashby, Lever, Workable,
SmartRecruiters and Teamtailor, so a firm like Archbau, whose openings live on
one plain HTML page, could not be followed even though the field asks for
"company.com/careers". This reads those pages: the anchors that look like job
adverts become postings, keyed by their URL, and the page is re-read on every
scan so a new advert appears the day it is published.

It is a reader, not a crawler: one page per employer, no links followed, and a
posting is whatever the employer linked to. Titles come from the anchor text
because that is the only title a plain page states.
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse

from ..models import Posting

NAME = "careers"
LABEL = "Employer pages"
BASE = ""

#: The words a link to a single advert uses, in the languages these pages are in.
_JOB_HREF = re.compile(
    r"(?:/jobs?/|/job-|/stelle|/stellenangebot|/karriere/|/careers?/|/vacanc|"
    r"/bewerb|/offene-stellen/|/open-positions?/|/positions?/)", re.I)
#: A link that is a section, not an advert.
_NOT_A_JOB = re.compile(
    r"(?:/jobs?/?$|/karriere/?$|/careers?/?$|/offene-stellen/?$|"
    r"/(?:impressum|datenschutz|privacy|kontakt|contact|about|team)(?:/|$))", re.I)
_RE_ANCHOR = re.compile(r"(?is)<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>")


def _text(fragment: str) -> str:
    import html as _html
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_page(page: str, base: str, company: str) -> list[Posting]:
    """The advert links on one careers page."""
    out: list[Posting] = []
    seen: set[str] = set()
    for href, inner in _RE_ANCHOR.findall(page or ""):
        url = urllib.parse.urljoin(base, href.strip())
        if not url.startswith("http") or url in seen:
            continue
        path = urllib.parse.urlsplit(url).path
        if not _JOB_HREF.search(path) or _NOT_A_JOB.search(path):
            continue
        title = _text(inner)
        if not 6 <= len(title) <= 140:
            continue
        seen.add(url)
        out.append(Posting(
            source=NAME, title=title, company=company, location="", url=url,
            external_id=hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
        ))
    return out


def parse_whole_page(page: str, base: str, company: str) -> list[Posting]:
    """One employer page with no per-advert links, kept as one posting.

    Archbau's jobs page is a single block of text: three vacancies, no links to
    any of them. There is nothing to split on, so the page itself is the
    posting, its title the page's, its body the text. One card per employer,
    refreshed on every scan, which is what "follow company.com/careers" can
    honestly mean for a page like this.
    """
    import html as _html
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", page or "")
    title = _text(m.group(1)) if m else f"{company} — openings"
    body = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", page or "")
    body = re.sub(r"(?i)</?(?:p|div|h[1-6]|li|br|tr)[^>]*>", "\n", body)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", body))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    description = "\n".join(l for l in lines if len(l) > 2)
    if len(description) < 200:
        return []
    return [Posting(source=NAME, title=title[:140], company=company, location="",
                    url=base, external_id=hashlib.sha1(base.encode("utf-8")).hexdigest()[:16],
                    description=description)]


def fetch(settings: dict, fetcher) -> list[Posting]:
    pages = settings.get("companies") or settings.get("urls") or []
    out: list[Posting] = []
    seen: set[str] = set()
    for url in pages:
        url = str(url).strip()
        if not url.startswith("http"):
            continue
        try:
            status, page = fetcher.get(url)
        except Exception as exc:                          # noqa: BLE001
            fetcher.errors.append(f"{NAME}/{url}: {exc}")
            continue
        if status != 200:
            fetcher.errors.append(f"{NAME}/{url}: HTTP {status}")
            continue
        company = urllib.parse.urlsplit(url).netloc.removeprefix("www.")
        found = parse_page(page, url, company) or parse_whole_page(page, url, company)
        for p in found:
            if p.external_id in seen:
                continue
            seen.add(p.external_id)
            out.append(p)
    return out
