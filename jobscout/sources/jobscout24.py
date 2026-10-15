"""JobScout24 (Switzerland) — server-rendered search, no key.

`GET /de/jobs/?SearchWhat=<kw>&SearchWhere=<loc>&p=<page>`. THE TRAP: the
pager parameter is `p`; `page=` is SILENTLY IGNORED (verified: `?page=2` came
back identical to page 1). That is exactly the class of bug this project
keeps paying for, so do not "fix" it back to `page`.

Cards are `li.job-list-item` with `data-job-id` and
`data-job-detail-url="/de/job/<uuid>/"`, a title anchor carrying a `title`
attribute, `job-attributes` spans (company, city) and a relative age
("1 W", "1 T") that is converted to an ISO date against today.
"""
from __future__ import annotations

import datetime as _dt
import re
from html import unescape
from urllib.parse import quote_plus

from ..http import Fetcher
from ..models import Posting

NAME = "jobscout24"
LABEL = "JobScout24"
API = "https://www.jobscout24.ch/de/jobs/"

_CARD = re.compile(r'<li class="job-list-item')
_DETAIL = re.compile(r'data-job-detail-url="(/de/job/[0-9a-f-]{36}/?)"')
_TITLE_ATTR = re.compile(r'class="job-link-detail[^"]*"[^>]*title="([^"]+)"')
_TITLE_TEXT = re.compile(r'class="job-link-detail[^"]*"[^>]*>([^<]+)</a>')
_ATTRS = re.compile(r'class="job-attributes">(.*?)</p>', re.S)
_SPAN = re.compile(r"<span>([^<]+)</span>")
_AGE = re.compile(r'class="job-date">\s*([^<]+?)\s*<')
_AGE_N = re.compile(r"(\d+)\s*([A-Za-z]+)")
_UNIT_DAYS = {"w": 7, "t": 1, "m": 30, "std": 0, "h": 0, "tag": 1, "woche": 7,
              "wochen": 7, "monat": 30, "monate": 30}


def _age_to_date(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    today = _dt.date.today()
    if s.lower() in ("heute", "today"):
        return today.isoformat()
    m = _AGE_N.search(s)
    if not m:
        return ""
    days = _UNIT_DAYS.get(m.group(2).lower())
    if days is None:
        return ""
    return (today - _dt.timedelta(days=int(m.group(1)) * days)).isoformat()


def parse(text: str) -> list[Posting]:
    parts = _CARD.split(text or "")
    out = []
    for chunk in parts[1:]:
        det = _DETAIL.search(chunk)
        if not det:
            continue
        tm = _TITLE_ATTR.search(chunk) or _TITLE_TEXT.search(chunk)
        title = unescape(tm.group(1)).strip() if tm else ""
        if not title:
            continue
        company = location = ""
        attrs = _ATTRS.search(chunk)
        if attrs:
            spans = _SPAN.findall(attrs.group(1))
            if spans:
                company = unescape(spans[0]).strip()
            if len(spans) > 1:
                location = unescape(spans[1]).strip()
        age = _AGE.search(chunk)
        out.append(Posting(
            source=NAME,
            title=title,
            company=company,
            location=location,
            url="https://www.jobscout24.ch" + det.group(1),
            external_id=det.group(1).strip("/").rsplit("/", 1)[-1],
            posted_at=(_age_to_date(age.group(1)) if age else ""),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for q in settings.get("queries", []):
        params = ["SearchWhat=" + quote_plus(str(q.get("keywords") or ""))]
        if q.get("location"):
            params.append("SearchWhere=" + quote_plus(str(q["location"])))
        base = f"{API}?{'&'.join(params)}"
        for page in range(1, int(q.get("pages", 1)) + 1):
            url = base if page == 1 else f"{base}&p={page}"
            try:
                status, text = fetcher.get(url)
            except Exception as e:
                fetcher.errors.append(f"{NAME}: {e}")
                break
            if status != 200:
                fetcher.errors.append(f"{NAME}: HTTP {status}")
                break
            out.extend(parse(text))
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
