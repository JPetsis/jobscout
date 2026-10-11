"""Hacker News 'Ask HN: Who is hiring?' — latest monthly thread via the public
Algolia + Firebase APIs."""
from __future__ import annotations

import json
import re

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html_lines, iso_from_epoch

NAME = "hn"
LABEL = "HN Who is hiring"
ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date"
FIREBASE = "https://hacker-news.firebaseio.com/v0/item/{}.json"

# First lines come in a few shapes:
#   Company | Role | Location | ...     (most common)
#   Company | Location | Type | Mode    (role lives further down)
#   Role | Location                     (freelance-style posts)
_ROLE = re.compile(
    r"\b(engineers?|developers?|designers?|scientists?|managers?|analysts?|architects?|"
    r"devops|sres?|programmers?|researchers?|consultants?|administrators?|founders?|"
    r"internships?|interns?|recruiters?|directors?|specialists?|coordinators?|officers?|leads?|"
    r"engineering|swe|engr|staff)\b",
    re.I,
)
_LOC_CUE = re.compile(
    r"\b(remote|onsite|on-site|hybrid|anywhere|worldwide|global|emea|apac|americas|europe)\b",
    re.I,
)
_COMPANY_SUFFIX = re.compile(r"\b(inc|gmbh|ltd|llc|ag|co|corp|oy|ab|bv|sa|srl|plc|kg|ug)\b\.?$", re.I)
_BOILERPLATE = re.compile(r"full[- ]?time|part[- ]?time|contract|freelance|internship|onsite|on-site|remote|hybrid", re.I)
_JUNK = " |,-*\u2022\u00b7"


def _clean_part(p: str) -> str:
    p = re.sub(r"https?://\S+", " ", p or "")
    p = re.sub(r"\(\s*\)", " ", p)           # empty parens left behind by a stripped link
    p = re.sub(r"\s+", " ", p).strip()
    p = re.sub(r"^location\s*:\s*", "", p, flags=re.I)
    p = re.sub(r"\s*\(\s*$", "", p)          # dangling "(" when the link ate the ")"
    return p.strip(_JUNK).strip()


def find_latest_story(fetcher: Fetcher) -> dict | None:
    status, text = fetcher.get(f"{ALGOLIA}?tags=story,author_whoishiring&hitsPerPage=8")
    if status != 200:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    for hit in data.get("hits", []):
        if "Who is hiring" in (hit.get("title") or ""):
            return hit
    return None


def parse_comment(cid, c: dict) -> Posting:
    """One top-level comment becomes one posting. First lines are loose by nature,
    so classify each ``|``-separated part instead of trusting fixed positions."""
    txt = clean_html_lines(c.get("text") or "")
    lines = [ln for ln in txt.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    parts = [p for p in (_clean_part(p) for p in first.split("|")) if p]

    role = next((p for p in parts if _ROLE.search(p) and len(p) <= 90), "")

    loc = ""
    ri = parts.index(role) if role else -1
    for i, p in enumerate(parts):  # comma shapes ("Berlin, Germany") win over cue words
        if i <= ri:  # a comma part before the role is usually the company, not a location
            continue
        if "," in p and len(p) <= 48 and "$" not in p and not _ROLE.search(p) and not _COMPANY_SUFFIX.search(p):
            loc = p
            break
    if not loc:
        for p in parts:
            if p != role and len(p) <= 48 and "$" not in p and _LOC_CUE.search(p):
                loc = p
                break

    company = ""
    for p in parts:
        if p in (role, loc) or len(p) > 60:
            continue
        if "$" in p or re.search(r"\b(salary|equity|compensation|bonus)\b", p, re.I):
            continue
        if "://" in p or p.lower().startswith("http"):
            continue
        if _LOC_CUE.search(p):
            continue
        if _BOILERPLATE.search(p) and len(p.split()) <= 3:
            continue
        company = p
        break

    if not role:  # no role signal: keep the first line (sans links) as the title
        prose = re.sub(r"https?://\S+", " ", first)
        role = " | ".join(b for b in (_clean_part(x) for x in prose.split("|")) if b)

    return Posting(
        source=NAME,
        title=(role or "HN post")[:120],
        company=company,
        location=loc,
        url=f"https://news.ycombinator.com/item?id={cid}",
        external_id=str(cid),
        posted_at=iso_from_epoch(c.get("time")),
        description=txt[:4000],
    )


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    max_comments = int(settings.get("max_comments", 120))
    story_hit = find_latest_story(fetcher)
    if not story_hit:
        fetcher.errors.append("hn: no 'Who is hiring' story found")
        return []
    try:
        story = fetcher.get_json(FIREBASE.format(story_hit["objectID"]), host_delay=0.05)
    except Exception as e:
        fetcher.errors.append(f"hn: {e}")
        return []
    out = []
    for kid in (story.get("kids") or [])[:max_comments]:
        try:
            c = fetcher.get_json(FIREBASE.format(kid), host_delay=0.05)
        except Exception:
            continue
        if not c or c.get("dead") or c.get("deleted") or not c.get("text"):
            continue
        out.append(parse_comment(kid, c))
    return out
