"""Python.org Jobs — the PSF job board, RSS.

Titles are "Role, Company" and the description begins with
`<location> <p>...`, so both are split back out here. Small feed (~20 live on
2026-10-15).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, clean_html_lines, xml_safe

NAME = "pythonjobs"
LABEL = "Python.org Jobs"
FEED = "https://www.python.org/jobs/feed/rss/"


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
        title, company = raw, ""
        if ", " in raw:
            title, company = [p.strip() for p in raw.rsplit(", ", 1)]
        desc_raw = item.findtext("description") or ""
        loc = clean_html(desc_raw.split("<p>", 1)[0]) if "<p>" in desc_raw else ""
        out.append(Posting(
            source=NAME,
            title=title,
            company=company,
            location=loc,
            url=link,
            external_id=link,
            posted_at=_rfc822((item.findtext("pubDate") or "").strip()),
            description=clean_html_lines(desc_raw)[:6000],
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
