"""Best-effort detail extraction for HTML job pages (JSON-LD / og:description)."""
from __future__ import annotations

import json
import re

from .http import Fetcher
from .textutil import clean_html

_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
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


def html_detail(url: str, fetcher: Fetcher) -> str | None:
    status, text = fetcher.get(url)
    if status != 200 or not text:
        return None
    for m in _LD.finditer(text):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for posting in _walk(data):
            desc = clean_html(str(posting.get("description") or ""))
            if desc:
                return desc
    m = _OG.search(text)
    if m:
        desc = clean_html(m.group(1))
        if desc:
            return desc
    return None
