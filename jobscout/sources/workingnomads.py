"""Working Nomads — public jobs API (remote roles).

One request returns the whole board as JSON; descriptions come inline as HTML.
Job URLs are tracking redirects (`/job/go/<id>/`), so the numeric id is used
as the stable external id.
"""
from __future__ import annotations

import re

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html

NAME = "workingnomads"
LABEL = "Working Nomads"
API = "https://www.workingnomads.com/api/exposed_jobs/"
_ID = re.compile(r"/go/(\d+)/")


def _date(s: str) -> str:
    s = (s or "").strip()
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else ""


def parse(data) -> list[Posting]:
    if not isinstance(data, list):
        return []
    out = []
    for it in data:
        title = (it.get("title") or "").strip()
        url = (it.get("url") or "").strip()
        if not title or not url:
            continue
        m = _ID.search(url)
        out.append(Posting(
            source=NAME,
            title=title,
            company=(it.get("company_name") or "").strip(),
            location=(it.get("location") or "").strip(),
            url=url,
            external_id=m.group(1) if m else url,
            posted_at=_date(it.get("pub_date")),
            description=clean_html(it.get("description") or "")[:6000],
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    try:
        data = fetcher.get_json(API)
    except Exception as e:
        fetcher.errors.append(f"workingnomads: {e}")
        return []
    return parse(data)
