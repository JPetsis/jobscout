"""jobs.ch (Switzerland) — public search API, JSON."""
from __future__ import annotations

from urllib.parse import urlencode

from ..http import Fetcher
from ..models import Posting
from ..textutil import date_part

NAME = "jobs_ch"
LABEL = "jobs.ch"
API = "https://www.jobs.ch/api/v1/public/search"


def _place(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("name") or v.get("fullLocation") or ""
    return ""


def parse(data: dict) -> list[Posting]:
    out = []
    for doc in data.get("documents", []):
        links = doc.get("_links") or {}
        url = ((links.get("detail_en") or links.get("detail_de") or {}) or {}).get("href", "")
        out.append(Posting(
            source=NAME,
            title=doc.get("title", ""),
            company=doc.get("company_name", ""),
            location=_place(doc.get("place")),
            url=url,
            external_id=str(doc.get("job_id") or doc.get("slug") or ""),
            posted_at=date_part(str(doc.get("publication_date") or doc.get("initial_publication_date") or "")),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        rows = min(int(q.get("rows", 20)), 20)  # the API caps rows at 20
        for page in range(1, int(q.get("pages", 1)) + 1):
            params = {"query": str(q.get("keywords", "")), "rows": rows, "start": (page - 1) * rows}
            if q.get("location"):
                params["location"] = str(q["location"])
            url = f"{API}?{urlencode(params)}"
            try:
                data = fetcher.get_json(url)
            except Exception as e:
                fetcher.errors.append(f"jobs_ch: {e}")
                continue
            out.extend(parse(data))
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
