"""NoDesk — RSS feed of remote roles.

Titles follow "<role> at <company>"; the feed carries no location element
(the whole board is remote), so the location is set to "Remote" like the
Berlin Startup Jobs adapter sets "Berlin". The feed ships HTML entities
(&rsquo;) that XML does not define, hence `xml_safe` before parsing.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, clean_html_lines, xml_safe

NAME = "nodesk"
LABEL = "NoDesk"
FEED = "https://nodesk.co/remote-jobs/index.xml"


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
        raw = clean_html((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        if not raw or not link:
            continue
        if " at " in raw:
            role, company = [p.strip() for p in raw.rsplit(" at ", 1)]
        else:
            role, company = raw, ""
        out.append(Posting(
            source=NAME,
            title=role,
            company=company,
            location="Remote",
            url=link,
            external_id=link,
            posted_at=_rfc822((item.findtext("pubDate") or "").strip()),
            description=clean_html_lines(item.findtext("description") or "")[:6000],
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    status, text = fetcher.get(FEED, host_delay=1.0)
    if status != 200:
        fetcher.errors.append(f"nodesk: HTTP {status}")
        return []
    return parse(text)
