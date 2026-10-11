"""SmartRecruiters company boards — public postings API (+ detail for descriptions)."""
from __future__ import annotations

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, date_part

NAME = "smartrecruiters"
LABEL = "SmartRecruiters"
API = "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100"
PAGE = "https://jobs.smartrecruiters.com/{token}/{id}"
DETAIL = "https://api.smartrecruiters.com/v1/companies/{token}/postings/{id}"


def parse(data: dict, token: str = "") -> list[Posting]:
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        location = loc.get("fullLocation") or ", ".join(
            p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p)
        out.append(Posting(
            source=NAME,
            title=j.get("name", ""),
            company=(j.get("company") or {}).get("name") or token,
            location=location,
            url=PAGE.format(token=token, id=j.get("id", "")),
            external_id=str(j.get("id", "")),
            posted_at=date_part(str(j.get("releasedDate") or "")),
            extra={"token": token},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for token in settings.get("companies", []):
        try:
            data = fetcher.get_json(API.format(token=token))
        except Exception as e:
            fetcher.errors.append(f"smartrecruiters/{token}: {e}")
            continue
        out.extend(parse(data, token))
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    token = (p.extra or {}).get("token")
    if not (token and p.external_id):
        return None
    try:
        data = fetcher.get_json(DETAIL.format(token=token, id=p.external_id))
    except Exception:
        return None
    sections = (data.get("jobAd") or {}).get("sections") or {}
    parts = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        sec = sections.get(key) or {}
        txt = sec.get("text") if isinstance(sec, dict) else None
        if txt:
            parts.append(clean_html(txt))
    return "\n\n".join(parts)[:8000] or None
