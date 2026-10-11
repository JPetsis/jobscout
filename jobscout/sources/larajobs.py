"""LaraJobs — RSS feed of Laravel/PHP roles.

The feed carries no description body, so the job page is read on demand
(fetch_detail). Some postings are hosted as JS shells (Notion, SPA careers
pages); there only the page's meta description is available.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html

NAME = "larajobs"
LABEL = "LaraJobs"
FEED = "https://larajobs.com/feed"

_DC = "{http://purl.org/dc/elements/1.1/}creator"
_JOB = "{https://larajobs.com}"
_MAIN = re.compile(r"<main\b[^>]*>(.*?)</main>", re.S | re.I)
_DROP = re.compile(r"<(script|style|nav|header|footer|aside|form|noscript)\b.*?</\1>",
                   re.S | re.I)
_OG = re.compile(
    r'<meta[^>]+(?:property|name)="(?:og:description|description)"[^>]+content="([^"]+)"',
    re.I)


def _rfc822(s: str) -> str:
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def _field(item, tag: str) -> str:
    el = item.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def parse(text: str) -> list[Posting]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = _field(item, "title")
        link = _field(item, "link")
        if not title or not link:
            continue
        tags = _field(item, f"{_JOB}tags")
        job_type = _field(item, f"{_JOB}job_type")
        blurb = " · ".join(p for p in (
            f"Tags: {tags}" if tags else "",
            f"Type: {job_type}" if job_type else "",
        ) if p)
        out.append(Posting(
            source=NAME,
            title=title,
            company=_field(item, f"{_JOB}company") or _field(item, _DC),
            location=_field(item, f"{_JOB}location"),
            url=link,
            external_id=link,
            posted_at=_rfc822(_field(item, "pubDate")),
            description=blurb,
            compensation=_field(item, f"{_JOB}salary"),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    status, text = fetcher.get(FEED, host_delay=1.0)
    if status != 200:
        fetcher.errors.append(f"larajobs: HTTP {status}")
        return []
    return parse(text)


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    """No body in the feed: read the job page. Theme-agnostic main-region
    text first, the page's meta description as fallback (JS shells)."""
    status, html = fetcher.get(p.url, host_delay=1.0)
    if status != 200 or not html:
        return None
    text = ""
    m = _MAIN.search(html)
    if m:
        text = re.sub(r"\s+", " ", clean_html(_DROP.sub(" ", m.group(1)))).strip()
    if len(text) < 200:
        og = _OG.search(html)
        cand = re.sub(r"\s+", " ", clean_html(og.group(1))).strip() if og else ""
        if len(cand) > len(text):
            text = cand
    return text[:8000] or None
