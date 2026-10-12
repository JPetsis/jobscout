"""We Work Remotely — RSS feeds per category."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, clean_html_lines

NAME = "wwr"
LABEL = "We Work Remotely"
BASE = "https://weworkremotely.com/categories/{}.rss"


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
        if not title:
            continue
        if ":" in title:
            company, role = [p.strip() for p in title.split(":", 1)]
        else:
            company, role = "", title
        link = (item.findtext("link") or item.findtext("url") or "").strip()
        region = (item.findtext("region") or "").strip()
        desc = clean_html_lines(item.findtext("description") or "")
        out.append(Posting(
            source=NAME,
            title=role,
            company=company,
            location=region,
            url=link,
            external_id=link,
            posted_at=_rfc822((item.findtext("pubDate") or "").strip()),
            description=desc[:6000],
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for cat in settings.get("categories", ["remote-programming-jobs"]):
        url = BASE.format(cat)
        status, text = fetcher.get(url, host_delay=1.0)
        if status != 200:
            fetcher.errors.append(f"wwr: HTTP {status} for category '{cat}'")
            continue
        out.extend(parse(text))
    return out
