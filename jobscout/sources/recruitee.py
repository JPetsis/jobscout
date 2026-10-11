"""Recruitee — public company boards API (`<host>/api/offers/`).

One JSON request per board, full HTML description inline, structured salary
fields. Posting identity is the numeric `id`, not the slug, so a slug rename
never duplicates a row.

A config entry may be either a bare token (`attendi`, which resolves to
`attendi.recruitee.com`) or a full host (`careers.tether.io`). This matters
more than it looks: most real boards are on a CUSTOM domain, and an adapter
that only builds `{token}.recruitee.com` silently skips every one of them —
it does not error, the board just never appears. Both `careers.tether.io` and
`makersite.io` were invisible that way.
"""
from __future__ import annotations

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, date_part

NAME = "recruitee"
LABEL = "Recruitee"
API = "https://{company}.recruitee.com/api/offers/"
API_HOST = "https://{host}/api/offers/"

_PERIOD = {"hour": "hour", "day": "day", "month": "month",
           "year": "year", "annual": "year"}


def _host_for(entry: str) -> str:
    """Config entry -> API host. A bare token gets the standard subdomain; a
    value that already looks like a host (has a dot, or is a URL) is used as-is.
    """
    token = entry.strip()
    if not token:
        return ""
    if token.startswith(("http://", "https://")):
        token = token.split("://", 1)[1].strip("/")
    if "/" in token:                      # someone pasted a board URL
        token = token.split("/", 1)[0]
    if "." in token:                      # already a host
        return token
    return f"{token}.recruitee.com"


def _compensation(o: dict) -> str:
    s = o.get("salary")
    if not isinstance(s, dict):
        return ""
    lo, hi = s.get("min"), s.get("max")
    if lo in (None, "") and hi in (None, ""):
        return ""
    try:
        lo_n = float(lo) if lo not in (None, "") else None
        hi_n = float(hi) if hi not in (None, "") else None
    except (TypeError, ValueError):
        return ""
    if lo_n is not None and hi_n is not None and lo_n != hi_n:
        amount = f"{lo_n:g}-{hi_n:g}"
    else:
        amount = f"{(lo_n if lo_n is not None else hi_n):g}"
    parts = [amount, (s.get("currency") or "").strip()]
    period = _PERIOD.get((s.get("period") or "").strip().lower(), "")
    if period:
        parts.append(f"per {period}")
    return " ".join(p for p in parts if p)


def parse(data, host: str = "") -> list[Posting]:
    """`host` is the board host, used only as a fallback label and URL base."""
    offers = data.get("offers") if isinstance(data, dict) else data
    if not isinstance(offers, list):
        return []
    out = []
    for o in offers:
        if not isinstance(o, dict):
            continue
        if str(o.get("status") or "published").lower() not in ("published", "", "open"):
            continue
        title = (o.get("title") or o.get("sharing_title") or "").strip()
        url = (o.get("careers_url") or "").strip()
        pid = o.get("id")
        if not title or not pid:
            continue
        loc = (o.get("location") or "").strip()
        if not loc and o.get("city"):
            loc = ", ".join(x for x in (o.get("city"), o.get("country")) if x)
        desc_parts = []
        for key in ("description", "requirements"):
            v = o.get(key)
            if isinstance(v, str) and v.strip():
                desc_parts.append(clean_html(v))
        # translations.en.description is the English variant on some boards
        tr = o.get("translations")
        if not desc_parts and isinstance(tr, dict):
            en = tr.get("en") if isinstance(tr.get("en"), dict) else None
            v = (en or {}).get("description")
            if isinstance(v, str):
                desc_parts.append(clean_html(v))
        # A board's own `url` field can point at a domain that no longer
        # resolves: decircletalentpartner.recruitee.com still serves all 196
        # deCircle offers, but each one advertises the retired
        # talent.makerdao.network, so every stored row 403/DNS-fails while the
        # posting is perfectly alive. The board host we successfully fetched is
        # the authority, so trust it and rebuild the canonical path.
        url = (o.get("url") or "").strip()
        if host and not url.startswith(f"https://{host}/"):
            url = f"https://{host}/o/{o.get('slug', '')}" if o.get("slug") else url
        out.append(Posting(
            source=NAME,
            title=title,
            company=(o.get("company_name") or host).strip(),
            location=loc,
            url=url or f"https://{host}/o/{o.get('slug', '')}",
            external_id=str(pid),
            posted_at=date_part(str(o.get("published_at") or o.get("created_at") or "")),
            description="\n\n".join(desc_parts)[:8000],
            compensation=_compensation(o),
            extra={"token": host, "remote": o.get("remote"),
                   "hybrid": o.get("hybrid")},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for entry in settings.get("companies", []):
        entry = (entry or "").strip()
        if not entry:
            continue
        host = _host_for(entry)
        if not host:
            continue
        try:
            data = fetcher.get_json(API_HOST.format(host=host))
        except Exception as e:
            fetcher.errors.append(f"recruitee/{host}: {e}")
            continue
        out.extend(parse(data, host))
    return out
