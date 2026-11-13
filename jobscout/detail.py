"""Best-effort detail extraction for HTML job pages (JSON-LD / og:description)."""
from __future__ import annotations

import json
import re

from .http import Fetcher
from .textutil import clean_html, clean_html_lines, strip_null_runs

# Any attributes may sit before `type=`. XING ships
# `<script data-ch type="application/ld+json">`, and the previous pattern
# required `type=` immediately after `<script `, so it did not match. The
# JSON-LD path therefore never ran on XING and the extractor fell through to
# og:description -- a social-sharing blurb of ~200 characters. That is why XING
# postings looked like they had no description when the page was carrying a
# 3,400-character one all along.
_LD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S)
_OG = re.compile(r'<meta[^>]+(?:property|name)="(?:og:description|description)"[^>]+content="([^"]+)"', re.I)


def _walk(obj):
    """Yield every JobPosting dict found in JSON-LD payloads (incl. @graph)."""
    if isinstance(obj, list):
        for o in obj:
            yield from _walk(o)
    elif isinstance(obj, dict):
        if obj.get("@type") == "JobPosting":
            yield obj
        if "@graph" in obj:
            yield from _walk(obj.get("@graph"))


# Every wording the boards use, in one place. XING's detail page prints the
# German pill "Zu den Ersten gehören" as visible text, while its search page
# ships the same fact as `job_highlight_early_applicant` in JSON; LinkedIn has
# "Be an early applicant" on the card and "first 25 applicants" in the advert.
_EARLY_SAID = re.compile(
    r"(first \d+ applicants|ersten \d+ Bewerber|early applicant"
    r"|zu den ersten geh(?:ö|oe)ren|job_highlight_early_applicant)", re.I)


def says_early_applicant(text: str) -> bool:
    """True when a page or an advert says you would be among the first.

    Works on extracted text AND on raw HTML: the badge usually sits in the page
    header, outside the advert body the extractor keeps, so a caller that has
    the page must pass the page. Silence is not a no.
    """
    return bool(_EARLY_SAID.search(text or ""))


def html_detail_verbose(url: str, fetcher: Fetcher) -> tuple[str, str]:
    desc, why, _ = html_detail_page(url, fetcher)
    return desc, why


def html_detail_raw(url: str, fetcher: Fetcher) -> tuple[str | None, str]:
    """The description and the RAW page, for sources that also read a badge.

    XING's detail page carries "Zu den Ersten gehören" in a marker pill outside
    the job description, so the extracted text cannot carry it; the source needs
    the page itself. Returning both keeps that to one request.
    """
    desc, _, raw = html_detail_page(url, fetcher)
    return (desc or None), raw


def html_detail_page(url: str, fetcher: Fetcher) -> tuple[str, str, str]:
    """(description, why when there is none, raw HTML).

    The pane has to be able to say what happened. "The listing was withdrawn"
    and "this board publishes no full text" look identical to someone staring at
    an empty box, and one of them means stop looking at this posting. Measured:
    XING answers HTTP 410 for a withdrawn listing, and the app was silently
    rendering nothing for a third of its rows.
    """
    if not url or not url.startswith("http"):
        return "", "This listing has no link, so there is nothing to open.", ""
    try:
        status, text = fetcher.get(url)
    except Exception as e:                                       # noqa: BLE001
        return "", f"Could not reach the page ({type(e).__name__}).", ""
    if status in (404, 410):
        return "", (f"The posting is gone: the site answered HTTP {status}, "
                    "which means it has been withdrawn."), text or ""
    if status != 200 or not text:
        return "", f"Could not read the page (HTTP {status}).", text or ""
    for m in _LD.finditer(text):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for posting in _walk(data):
            # clean_html_lines, not clean_html: the block boundaries are the
            # advert's formatting. Flattening them is what turned every Stepstone
            # and XING advert into one unreadable paragraph.
            desc = strip_null_runs(clean_html_lines(str(posting.get("description") or "")))
            if desc:
                return desc, "", text
    m = _OG.search(text)
    if m:
        desc = strip_null_runs(clean_html_lines(m.group(1)))
        if desc:
            return desc, "", text
    return "", ("The page loads, but it does not publish the full text of this "
                "listing. Open it on the site to read the whole advert."), text


def html_detail(url: str, fetcher: Fetcher) -> str | None:
    """Just the text, which is what the source adapters' contract needs."""
    return html_detail_verbose(url, fetcher)[0] or None
