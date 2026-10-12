"""Workable company boards — public widget API."""
from __future__ import annotations

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, clean_html_lines, date_part

NAME = "workable"
LABEL = "Workable"
API = "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"


def parse(data: dict, token: str = "") -> list[Posting]:
    out = []
    for j in data.get("jobs", []):
        location = ", ".join(p for p in (j.get("city"), j.get("country")) if p)
        if j.get("telecommuting") and "remote" not in location.lower():
            location = (location + " (Remote)").strip(", ")
        out.append(Posting(
            source=NAME,
            title=j.get("title", ""),
            company=token,
            location=location,
            url=j.get("url") or j.get("shortlink") or "",
            external_id=str(j.get("shortcode") or j.get("id") or ""),
            posted_at=date_part(str(j.get("published_on") or "")),
            description=clean_html_lines(j.get("description", ""))[:8000],
            extra={"token": token, "department": j.get("department")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for token in settings.get("companies", []):
        try:
            data = fetcher.get_json(API.format(token=token))
        except Exception as e:
            fetcher.errors.append(f"workable/{token}: {e}")
            continue
        out.extend(parse(data, token))
    return out
