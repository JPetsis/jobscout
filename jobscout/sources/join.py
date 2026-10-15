"""JOIN (join.com) — DACH SMB ATS with public per-company boards.

`GET /companies/<slug>` is server-rendered and contains ABSOLUTE job links
(`https://join.com/companies/<slug>/<id>-<title-slug>`); the company name
comes from the board's own Organization JSON-LD, the card `h3` is the title,
and the location sits behind a LocationPin icon. Detail pages carry a
schema.org JobPosting JSON-LD, which `fetch_detail` reads.

The sitemap files answer 403 Cloudflare, but the board pages themselves are
open (verified live 2026-10-15). Token-style: employers live under
`companies.join.tokens` as a bare slug or a pasted board URL.
"""
from __future__ import annotations

import json
import re

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html_lines

NAME = "join"
LABEL = "JOIN"

_TITLE = re.compile(r"<h3[^>]*>([^<]+)</h3>")
_LOC = re.compile(r"LocationPinIcon[\s\S]{0,2500}?</svg></i>([^<]+)<")
_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>([\s\S]*?)</script>')


def _slug_for(entry: str) -> str:
    t = (entry or "").strip()
    if not t:
        return ""
    if t.startswith(("http://", "https://")):
        t = t.split("://", 1)[1]
    if "join.com" in t:
        parts = [p for p in t.split("/") if p]
        for i, p in enumerate(parts):
            if p == "companies" and i + 1 < len(parts):
                return parts[i + 1].split("?")[0]
        return ""
    return t.split("/")[0].strip()


def _ld_objects(text: str) -> list:
    out = []
    for m in _LD.finditer(text or ""):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def parse(text: str, slug: str) -> list[Posting]:
    text = text or ""
    company = slug
    for obj in _ld_objects(text):
        if isinstance(obj, dict) and obj.get("@type") == "Organization" and obj.get("name"):
            company = str(obj["name"])
            break
    pat = re.compile(r'href="(https://join\.com/companies/'
                     + re.escape(slug) + r'/\d+[^"]*)"')
    hrefs = list(pat.finditer(text))
    out = []
    for i, m in enumerate(hrefs):
        end = (hrefs[i + 1].start() if i + 1 < len(hrefs)
               else min(len(text), m.start() + 8000))
        chunk = text[m.start():end]
        t = _TITLE.search(chunk)
        if not t or not t.group(1).strip():
            continue
        loc = _LOC.search(chunk)
        m_id = re.search(r"/(\d+)-", m.group(1))
        out.append(Posting(
            source=NAME,
            title=t.group(1).strip(),
            company=company,
            location=(loc.group(1).strip() if loc else ""),
            url=m.group(1),
            external_id=(m_id.group(1) if m_id else m.group(1)),
            extra={"board": slug},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for entry in settings.get("companies", []):
        slug = _slug_for(str(entry))
        if not slug:
            continue
        try:
            status, text = fetcher.get(f"https://join.com/companies/{slug}")
        except Exception as e:                                  # noqa: BLE001
            fetcher.errors.append(f"{NAME}/{slug}: {e}")
            continue
        if status != 200:
            fetcher.errors.append(f"{NAME}/{slug}: HTTP {status}")
            continue
        out.extend(parse(text, slug))
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    try:
        status, text = fetcher.get(p.url)
    except Exception:                                          # noqa: BLE001
        return None
    if status != 200:
        return None
    for obj in _ld_objects(text):
        if isinstance(obj, dict) and obj.get("@type") == "JobPosting":
            body = obj.get("description")
            if isinstance(body, str) and body.strip():
                return clean_html_lines(body)[:8000]
    return None
