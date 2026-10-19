"""FOSS Jobs (fossjobs.net) — free/open-source software jobs, RSS.

Titles are "[Type] Role at Company"; the description carries
"Location: ..." and the posting's real URL. Small feed (~10 live on
2026-10-15).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html_lines, xml_safe

NAME = "fossjobs"
LABEL = "FOSS Jobs"
FEED = "https://www.fossjobs.net/rss/all/"

_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
_LOC = re.compile(r"<strong>Location:</strong>\s*([^<]+)")


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
        raw = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not raw or not link:
            continue
        base = _PREFIX.sub("", raw).strip()
        title, company = base, ""
        if " at " in base:
            title, company = [p.strip() for p in base.rsplit(" at ", 1)]
        desc_raw = item.findtext("description") or ""
        loc = _LOC.search(desc_raw)
        out.append(Posting(
            source=NAME,
            title=title,
            company=company,
            location=(loc.group(1).strip() if loc else ""),
            url=link,
            external_id=link,
            posted_at=_rfc822((item.findtext("pubDate") or "").strip()),
            description=clean_html_lines(desc_raw)[:6000],
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    try:
        status, text = fetcher.get(FEED)
    except Exception as e:                                      # noqa: BLE001
        fetcher.errors.append(f"{NAME}: {e}")
        return []
    if status != 200:
        fetcher.errors.append(f"{NAME}: HTTP {status}")
        return []
    return parse(text)
