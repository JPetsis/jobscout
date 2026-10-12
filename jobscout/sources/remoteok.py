"""Remote OK — public JSON feed."""
from __future__ import annotations

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, clean_html_lines, date_part

NAME = "remoteok"
LABEL = "Remote OK"
API = "https://remoteok.com/api"


def parse(data) -> list[Posting]:
    out = []
    if not isinstance(data, list):
        return out
    for it in data:
        if not isinstance(it, dict) or "position" not in it:
            continue  # first element is the legal notice
        sal = ""
        if it.get("salary_min") and it.get("salary_max"):
            sal = f"${it['salary_min']}-{it['salary_max']}"
        out.append(Posting(
            source=NAME,
            title=it.get("position", ""),
            company=it.get("company", ""),
            location=it.get("location", ""),
            url=it.get("url", "") or it.get("apply_url", ""),
            external_id=str(it.get("id", "")),
            posted_at=date_part(str(it.get("date") or "")),
            description=clean_html_lines(it.get("description", ""))[:6000],
            compensation=sal,
            extra={"tags": it.get("tags")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    try:
        data = fetcher.get_json(API)
    except Exception as e:
        fetcher.errors.append(f"remoteok: {e}")
        return []
    return parse(data)
