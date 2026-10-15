"""jobup.ch (Switzerland, French-speaking) — public search API, JSON.

Same platform as jobs.ch (both are JobCloud), and the response envelope is the
same one the jobs_ch adapter already parses: `documents[]` with title,
company_name, place, job_id, slug, publication_date and `_links`. The detail
link key differs by language (jobup exposes detail_fr/detail_en where jobs.ch
has detail_de/detail_en), which is why this is a sibling module and not a
host parameter -- the parser is small and the two stay independently testable.

Verified live 2026-10-15: a real INFOMANIAK PHP posting came back through this
exact endpoint.
"""
from __future__ import annotations

from urllib.parse import urlencode

from ..http import Fetcher
from ..models import Posting
from ..textutil import date_part

NAME = "jobup"
LABEL = "jobup.ch"
API = "https://www.jobup.ch/api/v1/public/search"


def _place(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("name") or v.get("fullLocation") or ""
    return ""


def _detail(doc: dict) -> str:
    links = doc.get("_links") or {}
    for key in ("detail_en", "detail_fr", "detail_de"):
        href = ((links.get(key) or {}) or {}).get("href", "")
        if href:
            return href
    return ""


def parse(data: dict) -> list[Posting]:
    out = []
    for doc in data.get("documents", []):
        out.append(Posting(
            source=NAME,
            title=doc.get("title", ""),
            company=doc.get("company_name", ""),
            location=_place(doc.get("place")),
            url=_detail(doc),
            external_id=str(doc.get("job_id") or doc.get("slug") or ""),
            posted_at=date_part(str(doc.get("publication_date")
                                    or doc.get("initial_publication_date") or "")),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        rows = min(int(q.get("rows", 20)), 20)  # same API cap as jobs.ch
        for page in range(1, int(q.get("pages", 1)) + 1):
            params = {"query": str(q.get("keywords", "")), "rows": rows,
                      "start": (page - 1) * rows}
            if q.get("location"):
                params["location"] = str(q["location"])
            url = f"{API}?{urlencode(params)}"
            try:
                data = fetcher.get_json(url)
            except Exception as e:
                fetcher.errors.append(f"{NAME}: {e}")
                continue
            out.extend(parse(data))
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
