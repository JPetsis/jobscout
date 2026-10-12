"""Best-effort detail extraction for HTML job pages (JSON-LD / og:description)."""
from __future__ import annotations

import json
import re

from .http import Fetcher
from .textutil import clean_html

# Any attributes may sit before `type=`. XING ships
# `<script data-ch type="application/ld+json">`, and the previous pattern
# required `type=` immediately after `<script `, so it did not match. The
# JSON-LD path therefore never ran on XING and the extractor fell through to
# og:description -- a social-sharing blurb of ~200 characters. That is why XING
# postings looked like they had no description when the page was carrying a
# 3,400-character one all along.
_LD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S)
_OG = re.compile(r'<meta[^>]+(?:property|name)="(?:og:description|description)"[^>]+content="([^"]+)"', re.I)


def _walk(obj):
    """Yield every JobPosting dict found in JSON-LD payloads (incl. @graph)."""
    if isinstance(obj, list):
        for o in obj:
            yield from _walk(o)
    elif isinstance(obj, dict):
        if obj.get("@type") == "JobPosting":
            yield obj
        if "@graph" in obj:
            yield from _walk(obj.get("@graph"))


def html_detail_verbose(url: str, fetcher: Fetcher) -> tuple[str, str]:
    """The description, and when there is none, WHY.

    The pane has to be able to say what happened. "The listing was withdrawn"
    and "this board publishes no full text" look identical to someone staring at
    an empty box, and one of them means stop looking at this posting. Measured:
    XING answers HTTP 410 for a withdrawn listing, and the app was silently
    rendering nothing for a third of its rows.
    """
    if not url or not url.startswith("http"):
        return "", "This listing has no link, so there is nothing to open."
    try:
        status, text = fetcher.get(url)
    except Exception as e:                                       # noqa: BLE001
        return "", f"Could not reach the page ({type(e).__name__})."
    if status in (404, 410):
        return "", (f"The posting is gone: the site answered HTTP {status}, "
                    "which means it has been withdrawn.")
    if status != 200 or not text:
        return "", f"Could not read the page (HTTP {status})."
    for m in _LD.finditer(text):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for posting in _walk(data):
            desc = clean_html(str(posting.get("description") or ""))
            if desc:
                return desc, ""
    m = _OG.search(text)
    if m:
        desc = clean_html(m.group(1))
        if desc:
            return desc, ""
    return "", ("The page loads, but it does not publish the full text of this "
                "listing. Open it on the site to read the whole advert.")


def html_detail(url: str, fetcher: Fetcher) -> str | None:
    """Just the text, which is what the source adapters' contract needs."""
    return html_detail_verbose(url, fetcher)[0] or None
