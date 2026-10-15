"""WordPress Jobs (jobs.wordpress.net) — the official WP job board, RSS.

Small feed (5 live items on 2026-10-15), plain RSS 2.0: title, link, pubDate,
description. The feed carries no company or location fields, so those stay
empty and the scorer works from title/description like every other source.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html_lines, xml_safe

NAME = "wordpressjobs"
LABEL = "WordPress Jobs"
FEED = "https://jobs.wordpress.net/feed/"
_DESC = "{http://purl.org/rss/1.0/modules/content/}encoded"


def _rfc822(s: str) -> str:
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def parse(text: str) -> list[Posting]:
    try:
        root = ET.fromstring(xml_safe(text))
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        desc = ""
        for tag in (_DESC, "description"):
            el = item.find(tag)
            if el is not None and el.text:
                desc = clean_html_lines(el.text)
                break
        out.append(Posting(
            source=NAME,
            title=title,
            url=link,
            external_id=link,
            posted_at=_rfc822((item.findtext("pubDate") or "").strip()),
            description=desc[:6000],
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    try:
        status, text = fetcher.get(FEED, host_delay=1.0)
    except Exception as e:                                      # noqa: BLE001
        fetcher.errors.append(f"{NAME}: {e}")
        return []
    if status != 200:
        fetcher.errors.append(f"{NAME}: HTTP {status}")
        return []
    return parse(text)
