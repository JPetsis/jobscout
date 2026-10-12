"""Arbeitnow — free public job board API (Germany-friendly, global remote)."""
from __future__ import annotations

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, clean_html_lines, iso_from_epoch

NAME = "arbeitnow"
LABEL = "Arbeitnow"
API = "https://www.arbeitnow.com/api/job-board-api"


def parse(data: dict) -> list[Posting]:
    out = []
    for it in data.get("data", []):
        out.append(Posting(
            source=NAME,
            title=it.get("title", ""),
            company=it.get("company_name", ""),
            location=it.get("location", ""),
            url=it.get("url", ""),
            external_id=str(it.get("slug", "")),
            posted_at=iso_from_epoch(it.get("created_at")),
            description=clean_html_lines(it.get("description", ""))[:6000],
            extra={"tags": it.get("tags"), "remote": it.get("remote")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for page in range(1, int(settings.get("pages", 1)) + 1):
        url = API if page == 1 else f"{API}?page={page}"
        try:
            data = fetcher.get_json(url)
        except Exception as e:
            fetcher.errors.append(f"arbeitnow: {e}")
            break
        out.extend(parse(data))
    return out
