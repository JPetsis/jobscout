"""Teamtailor — per-company boards with a public JSON Feed.

Every Teamtailor board exposes `https://<company>.teamtailor.com/jobs.json`:
JSON Feed 1.1, 100 items per page, `next_url` = `?page=2&per_page=100`, and
each item carries `_jobposting`, a full schema.org JobPosting (locations,
dates, company, description). Verified live 2026-10-15: TeamViewer returned
100 items with a next_url, Sandbox Interactive 6 with none.

Token-style like the other ATS sources: employers live under
`companies.teamtailor.tokens` in config, as a bare token (`teamviewer`), a
full host, or a pasted board URL.
"""
from __future__ import annotations

import json

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html_lines, date_part

NAME = "teamtailor"
LABEL = "Teamtailor"


def _host_for(entry: str) -> str:
    token = (entry or "").strip()
    if not token:
        return ""
    if token.startswith(("http://", "https://")):
        token = token.split("://", 1)[1].strip("/")
    if "/" in token:                      # a pasted board URL
        token = token.split("/", 1)[0]
    if "." in token:
        return token
    return f"{token}.teamtailor.com"


def parse_feed(data: dict, host: str) -> list[Posting]:
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    board = str(data.get("title") or host).strip()
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        jp_raw = it.get("_jobposting")
        jp = jp_raw if isinstance(jp_raw, dict) else {}
        title = str(jp.get("title") or it.get("title") or "").strip()
        url = str(it.get("url") or "").strip()
        if not title or not url:
            continue
        locs = []
        jl = jp.get("jobLocation")
        for place in (jl if isinstance(jl, list) else [jl]):
            addr = place.get("address") if isinstance(place, dict) else None
            if not isinstance(addr, dict):
                continue
            line = ", ".join(str(b) for b in (
                addr.get("addressLocality"), addr.get("addressRegion"),
                addr.get("addressCountry")) if b)
            if line and line not in locs:
                locs.append(line)
        org = jp.get("hiringOrganization")
        org_name = str(org.get("name")) if isinstance(org, dict) and org.get("name") else board
        desc = clean_html_lines(str(it.get("content_html")
                                    or jp.get("description") or ""))
        out.append(Posting(
            source=NAME,
            title=title,
            company=org_name,
            location="; ".join(locs),
            url=url,
            external_id=str(it.get("id") or url),
            posted_at=date_part(str(jp.get("datePosted")
                                    or it.get("date_published") or "")),
            description=desc[:8000],
            extra={"board": host},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    max_pages = max(1, int(settings.get("max_pages", 2) or 2))
    out: list[Posting] = []
    for entry in settings.get("companies", []):
        host = _host_for(str(entry))
        if not host:
            continue
        url = f"https://{host}/jobs.json"
        for _ in range(max_pages):
            try:
                status, text = fetcher.get(url)
            except Exception as e:
                fetcher.errors.append(f"{NAME}/{host}: {e}")
                break
            if status != 200:
                fetcher.errors.append(f"{NAME}/{host}: HTTP {status}")
                break
            try:
                data = json.loads(text)
            except ValueError as e:
                fetcher.errors.append(f"{NAME}/{host}: invalid JSON: {e}")
                break
            out.extend(parse_feed(data, host))
            nxt = data.get("next_url") if isinstance(data, dict) else None
            if not nxt:
                break
            url = str(nxt)
    return out
