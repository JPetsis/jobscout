"""EURES — the EU's official job vacancy portal (public JSON search API).

Endpoint map (reverse-engineered OpenAPI, verified live): base
`https://europa.eu/eures/api`, search
`POST /jv-searchengine/public/jv-search/search`, vacancy page
`https://europa.eu/eures/portal/jv/id/{id}.aspx?lang=en`.

What the live API taught us:
* `resultsPerPage` maxes at 50 (100+ answers HTTP 400).
* `locationCodes` are NUTS codes: country-level lowercase (`at`, `de`), or
  `at-NS` for "Austria, location not specified". An empty list means all of
  the EU, so the adapter always sends explicit countries.
* `keywords[].specificSearchCode` scopes the match: `EVERYWHERE` is very
  fuzzy (a KFZ mechanic matched "software engineer" through the body),
  `TITLE` is precise. Default is TITLE.
* Descriptions arrive as HTML under `translations[<lang>].description`; the
  top-level `title` mirrors the first-language title.
* `europa.eu` serves a GlobalSign chain absent from the macOS trust store, so
  the CLI builds the Fetcher with a certifi context. Never disable
  verification; see `cmd_scan` in cli.py.
* Sorting is MOST_RECENT, so paging from page 1 walks newest-first and the
  scan can stop once postings fall outside the age window.
"""
from __future__ import annotations

import datetime as _dt
import urllib.parse

from ..http import Fetcher, FetchError
from ..models import Posting
from ..textutil import clean_html, clean_html_lines, iso_from_epoch

NAME = "eures"
LABEL = "EURES"
API = "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search"
PORTAL = "https://europa.eu/eures/portal/jv/id/{id}.aspx?lang=en"

_COUNTRIES = {
    "at": "Austria", "de": "Germany", "ch": "Switzerland", "nl": "Netherlands",
    "fr": "France", "it": "Italy", "es": "Spain", "pt": "Portugal",
    "be": "Belgium", "ie": "Ireland", "lu": "Luxembourg", "sk": "Slovakia",
    "si": "Slovenia", "pl": "Poland", "cz": "Czechia", "hu": "Hungary",
    "hr": "Croatia", "dk": "Denmark", "se": "Sweden", "fi": "Finland",
    "no": "Norway", "ee": "Estonia", "lv": "Latvia", "lt": "Lithuania",
    "bg": "Bulgaria", "ro": "Romania", "gr": "Greece", "mt": "Malta",
    "cy": "Cyprus",
}


def _country_name(code: str) -> str:
    return _COUNTRIES.get(code.lower(), code.upper())


def _location(jv: dict) -> str:
    return ", ".join(_country_name(str(k)) for k in (jv.get("locationMap") or {}))


def _text(jv: dict) -> tuple[str, str]:
    """(title, description), honouring the posting's own language."""
    trs = jv.get("translations") or {}
    lang = next((l for l in (jv.get("availableLanguages") or []) if l in trs), None)
    node = trs.get(lang) if lang else None
    if not isinstance(node, dict):
        node = next((v for v in trs.values() if isinstance(v, dict)), {})
    title = (node.get("title") or jv.get("title") or "").strip()
    desc = clean_html_lines(node.get("description") or jv.get("description") or "")
    return title, desc


def parse(jvs) -> list[Posting]:
    out = []
    for jv in jvs or []:
        if not isinstance(jv, dict):
            continue
        ident = (jv.get("id") or "").strip()
        title, desc = _text(jv)
        if not ident or not title:
            continue
        emp = jv.get("employer") if isinstance(jv.get("employer"), dict) else {}
        out.append(Posting(
            source=NAME,
            title=title,
            company=(emp.get("name") or "").strip(),
            location=_location(jv),
            url=PORTAL.format(id=urllib.parse.quote(ident, safe="")),
            external_id=ident,
            posted_at=iso_from_epoch(jv.get("creationDate")),
            description=desc[:8000],
            extra={"nuts": sorted((jv.get("locationMap") or {}).keys()),
                   "eures": bool(jv.get("euresFlag")),
                   "numberOfPosts": jv.get("numberOfPosts")},
        ))
    return out


def _payload(keyword: str, where: str, page: int, per_page: int, settings: dict) -> dict:
    return {
        "resultsPerPage": per_page,
        "page": page,
        "sortSearch": "MOST_RECENT",
        "keywords": [{"keyword": keyword,
                      "specificSearchCode": settings.get("search_code", "TITLE")}],
        "publicationPeriod": settings.get("publication_period", "LAST_MONTH"),
        "occupationUris": [], "skillUris": [], "requiredExperienceCodes": [],
        "positionScheduleCodes": [], "sectorCodes": [],
        "educationAndQualificationLevelCodes": [], "positionOfferingCodes": [],
        "locationCodes": [where] if where else [],
        "euresFlagCodes": [], "otherBenefitsCodes": [], "requiredLanguages": [],
        "minNumberPost": None,
        "sessionId": settings.get("session_id", "jobscout"),
        "requestLanguage": settings.get("language", "en"),
    }


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    queries = settings.get("queries") or [
        {"keywords": "software engineer", "location": "at"},
        {"keywords": "software developer", "location": "at"},
        {"keywords": "developer", "location": "at"},
        {"keywords": "devops", "location": "at"},
    ]
    max_pages = int(settings.get("max_pages", 2))
    per_page = min(int(settings.get("per_page", 50)), 50)   # API rejects >50
    max_age_days = int(settings.get("max_age_days", 45))
    cutoff = (_dt.date.today() - _dt.timedelta(days=max_age_days)).isoformat()
    out: list[Posting] = []
    seen: set[str] = set()
    for q in queries:
        keyword = (q.get("keywords") or "").strip()
        if not keyword:
            continue
        where = (q.get("location") or "").strip()
        for page in range(1, max_pages + 1):
            try:
                data = fetcher.post_json(
                    API, _payload(keyword, where, page, per_page, settings))
            except (FetchError, ValueError) as e:
                fetcher.errors.append(f"eures/{keyword}: {e}")
                break
            batch = parse(data.get("jvs") or [])
            if not batch:
                break
            for p in batch:
                if p.external_id not in seen:
                    seen.add(p.external_id)
                    out.append(p)
            if min((p.posted_at or "9999") for p in batch) < cutoff:
                break                   # walked past the age window
            if len(batch) < per_page:
                break
    return out
