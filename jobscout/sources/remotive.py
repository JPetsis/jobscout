"""Remotive — free public API of remote jobs."""
from __future__ import annotations

from urllib.parse import urlencode

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, clean_html_lines, date_part

NAME = "remotive"
LABEL = "Remotive"
API = "https://remotive.com/api/remote-jobs"


def parse(data: dict) -> list[Posting]:
    out = []
    for j in data.get("jobs", []):
        out.append(Posting(
            source=NAME,
            title=j.get("title", ""),
            company=j.get("company_name", ""),
            location=j.get("candidate_required_location", ""),
            url=j.get("url", ""),
            external_id=str(j.get("id", "")),
            posted_at=date_part(str(j.get("publication_date") or "")),
            description=clean_html_lines(j.get("description", ""))[:6000],
            extra={"category": j.get("category"), "job_type": j.get("job_type")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    params = {"limit": int(settings.get("limit", 100))}
    search = str(settings.get("search") or "").strip()
    if search:
        params["search"] = search
    try:
        data = fetcher.get_json(f"{API}?{urlencode(params)}")
    except Exception as e:
        fetcher.errors.append(f"remotive: {e}")
        return []
    return parse(data)
