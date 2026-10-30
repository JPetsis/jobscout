"""SmartRecruiters company boards — public postings API (+ detail for descriptions)."""
from __future__ import annotations

import re

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, date_part

NAME = "smartrecruiters"
LABEL = "SmartRecruiters"
API = "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100"
PAGE = "https://jobs.smartrecruiters.com/{token}/{id}"
DETAIL = "https://api.smartrecruiters.com/v1/companies/{token}/postings/{id}"
# The board URL carries the same two halves as the API path, which matters
# because `extra` is in-memory only and does not survive a round trip through
# the store.
_PAGE = re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)/([0-9A-Za-z]+)")


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
    """The full advert, from the posting API rather than the rendered page.

    The page at jobs.smartrecruiters.com is a JavaScript app: its HTML carries a
    browser-support banner and no advert text, so a generic HTML probe "loads
    the page" and finds nothing. The API has all four sections.

    `extra` is only set on a fresh posting; the window and `jobscout show` read
    a row back from the store, where it is gone. Falling back to the URL is what
    makes the detail work there -- without it this returned None and every
    SmartRecruiters advert opened blank.
    """
    token = (p.extra or {}).get("token")
    pid = p.external_id
    if not (token and pid):
        m = _PAGE.search(p.url or "")
        if m:
            token = token or m.group(1)
            pid = pid or m.group(2)
    if not (token and pid):
        return None
    try:
        data = fetcher.get_json(DETAIL.format(token=token, id=pid))
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
