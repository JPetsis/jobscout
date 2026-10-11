"""Ashby company boards — public posting API."""
from __future__ import annotations

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, date_part

NAME = "ashby"
LABEL = "Ashby"
API = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"


def parse(data: dict, token: str = "") -> list[Posting]:
    out = []
    for j in data.get("jobs", []):
        location = str(j.get("location") or "")
        if j.get("isRemote") and "remote" not in location.lower():
            location = (location + " (Remote)").strip()
        comp = j.get("compensation") if isinstance(j.get("compensation"), dict) else {}
        comp_s = (comp or {}).get("compensationTierSummary") or (comp or {}).get("scrapeableCompensationSalarySummary") or ""
        desc = j.get("descriptionPlain") or clean_html(j.get("descriptionHtml", ""))
        out.append(Posting(
            source=NAME,
            title=j.get("title", ""),
            company=token,
            location=location,
            url=j.get("jobUrl", ""),
            external_id=str(j.get("id", "")),
            posted_at=date_part(str(j.get("publishedAt") or "")),
            description=(desc or "")[:8000],
            compensation=str(comp_s),
            extra={"token": token, "department": j.get("department"), "team": j.get("team")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for token in settings.get("companies", []):
        try:
            data = fetcher.get_json(API.format(token=token))
        except Exception as e:
            fetcher.errors.append(f"ashby/{token}: {e}")
            continue
        out.extend(parse(data, token))
    return out
