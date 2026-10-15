"""GermanTechJobs — RSS feed of German IT roles (germantechjobs.de/rss).

One request returns the board (830 items live on 2026-10-15). Every title is
"Role @ Company [salary]" and the description repeats the salary plus
requirements. Item links carry tracking (`?utm_source=our_rss_feed`) which is
stripped so the stored URL is the canonical job page. The feed carries no
location field, so `location` stays empty (unknown locations are kept by the
geo gate).

SwissDevJobs runs the same platform: swissdevjobs.py imports this module's
parser and feed helper rather than re-implementing them.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html_lines

NAME = "germantechjobs"
LABEL = "GermanTechJobs"
FEED = "https://germantechjobs.de/rss"

_DESC = "{http://purl.org/rss/1.0/modules/content/}encoded"
_SALARY = re.compile(r"\s*\[([^\]]+)\]\s*$")


def _rfc822(s: str) -> str:
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def parse_items(text: str, source: str) -> list[Posting]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip().split("?", 1)[0]
        if not raw_title or not link:
            continue
        m = _SALARY.search(raw_title)
        comp = m.group(1).strip() if m else ""
        base = (_SALARY.sub("", raw_title) if m else raw_title).strip()
        title, company = base, ""
        if " @ " in base:
            title, company = [p.strip() for p in base.rsplit(" @ ", 1)]
        desc = ""
        for tag in (_DESC, "description"):
            el = item.find(tag)
            if el is not None and el.text:
                desc = clean_html_lines(el.text)
                break
        out.append(Posting(
            source=source,
            title=title,
            company=company,
            url=link,
            external_id=link,
            posted_at=_rfc822((item.findtext("pubDate") or "").strip()),
            description=desc[:6000],
            compensation=comp,
        ))
    return out


def fetch_feed(fetcher: Fetcher, url: str, source: str) -> list[Posting]:
    try:
        status, text = fetcher.get(url, host_delay=1.0)
    except Exception as e:                                      # noqa: BLE001
        fetcher.errors.append(f"{source}: {e}")
        return []
    if status != 200:
        fetcher.errors.append(f"{source}: HTTP {status}")
        return []
    return parse_items(text, source)


def parse(text: str) -> list[Posting]:
    return parse_items(text, NAME)


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    return fetch_feed(fetcher, FEED, NAME)
