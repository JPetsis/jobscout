"""Greenhouse company boards — public job board API."""
from __future__ import annotations

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, date_part

NAME = "greenhouse"
LABEL = "Greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def parse(data: dict, token: str = "") -> list[Posting]:
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location")
        if isinstance(loc, dict):
            loc = loc.get("name", "")
        out.append(Posting(
            source=NAME,
            title=j.get("title", ""),
            company=j.get("company_name") or token,
            location=str(loc or ""),
            url=j.get("absolute_url", ""),
            external_id=str(j.get("id", "")),
            posted_at=date_part(str(j.get("first_published") or j.get("updated_at") or "")),
            description=clean_html(j.get("content", ""))[:8000],
            extra={"token": token},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for token in settings.get("companies", []):
        try:
            data = fetcher.get_json(API.format(token=token))
        except Exception as e:
            fetcher.errors.append(f"greenhouse/{token}: {e}")
            continue
        out.extend(parse(data, token))
    return out
