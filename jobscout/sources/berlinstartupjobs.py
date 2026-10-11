"""Berlin Startup Jobs — WordPress RSS feed of Berlin startup roles."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html

NAME = "berlinstartupjobs"
LABEL = "Berlin Startup Jobs"
FEED = "https://berlinstartupjobs.com/feed/"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"


def _rfc822(s: str) -> str:
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def parse(text: str) -> list[Posting]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title:
            continue
        if "//" in title:
            role, company = [p.strip() for p in title.split("//", 1)]
        else:
            role, company = title, ""
        desc = ""
        for tag in (_CONTENT, "description"):
            el = item.find(tag)
            if el is not None and el.text:
                desc = clean_html(el.text)
                break
        out.append(Posting(
            source=NAME,
            title=role,
            company=company,
            location="Berlin",
            url=link,
            external_id=link,
            posted_at=_rfc822((item.findtext("pubDate") or "").strip()),
            description=desc[:6000],
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    status, text = fetcher.get(FEED, host_delay=1.0)
    if status != 200:
        fetcher.errors.append(f"berlinstartupjobs: HTTP {status}")
        return []
    return parse(text)
