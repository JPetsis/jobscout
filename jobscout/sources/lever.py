"""Lever company boards — public postings API."""
from __future__ import annotations

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html, iso_from_epoch

NAME = "lever"
LABEL = "Lever"
API = "https://api.lever.co/v0/postings/{token}?mode=json"


def parse(data: list, token: str = "") -> list[Posting]:
    out = []
    if not isinstance(data, list):
        return out
    for j in data:
        if not isinstance(j, dict):
            continue
        cats = j.get("categories") or {}
        desc = j.get("descriptionPlain") or clean_html(j.get("description", ""))
        out.append(Posting(
            source=NAME,
            title=j.get("text", ""),
            company=token,
            location=str(cats.get("location") or ""),
            url=j.get("hostedUrl", ""),
            external_id=str(j.get("id", "")),
            posted_at=iso_from_epoch(j.get("createdAt")),
            description=(desc or "")[:8000],
            extra={"team": cats.get("team"), "token": token},
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    for token in settings.get("companies", []):
        try:
            data = fetcher.get_json(API.format(token=token))
        except Exception as e:
            fetcher.errors.append(f"lever/{token}: {e}")
            continue
        out.extend(parse(data, token))
    return out
