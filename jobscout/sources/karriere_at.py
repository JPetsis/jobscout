"""karriere.at (Austria) — result list lives in window.VUE_INITIAL_STATE."""
from __future__ import annotations

import json
import re

from ..http import Fetcher
from ..models import Posting
from ..textutil import balanced_json, de_rel_date, slug

NAME = "karriere_at"
LABEL = "karriere.at"
BASE = "https://www.karriere.at"
_STATE_MARK = "window.VUE_INITIAL_STATE = "


def parse(text: str) -> list[Posting]:
    start = text.find(_STATE_MARK)
    if start < 0:
        return []
    found = balanced_json(text, start)
    if not found:
        return []
    raw, _ = found
    try:
        state = json.loads(raw)
    except ValueError:
        return []
    items = (((state.get("jobsSearchList") or {}).get("activeItems") or {}).get("items")) or []
    out = []
    for wrapper in items:
        j = wrapper.get("jobsItem") if isinstance(wrapper, dict) else None
        if not isinstance(j, dict) or not j.get("title"):
            continue
        company = (j.get("company") or {}).get("name") or ""
        locs = ", ".join(loc.get("name", "") for loc in (j.get("locations") or []) if loc.get("name"))
        summary = j.get("summary") or ""
        snip = j.get("snippet") or ""
        desc = (summary + "\n" + snip).strip()
        out.append(Posting(
            source=NAME,
            title=j.get("title", ""),
            company=company,
            location=locs or (j.get("company") or {}).get("mainLocation") or "",
            url=j.get("link") or "",
            external_id=str(j.get("id") or ""),
            posted_at=de_rel_date(j.get("date") or ""),
            description=desc,
            compensation=j.get("salary") or "",
            extra={"homeoffice": j.get("isHomeOffice"), "employment": j.get("employmentTypes")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        kw = slug(str(q.get("keywords", "")))
        for page in range(1, int(q.get("pages", 1)) + 1):
            url = f"{BASE}/jobs/{kw}?page={page}"
            status, text = fetcher.get(url, host_delay=1.0)
            if status != 200:
                fetcher.errors.append(f"karriere_at: HTTP {status} for '{q.get('keywords')}' page {page}")
                continue
            out.extend(parse(text))
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
