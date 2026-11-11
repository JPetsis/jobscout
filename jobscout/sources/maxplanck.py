"""Max Planck Society jobboard — the employer's own vacancies, every institute.

Not a keyword search: the board lists every open position, five on the page and
the rest through `/jobboard/<id>/more_items`, which answers an HTML fragment only
when asked as an XHR. Pagination is limit/offset, so a scan walks the board once
and the profile decides afterwards, like every other broad source.

The listing gives a title, a date, the institute and the city; the advert itself
lives on a server-rendered page and is read on demand by the fill job.
"""
from __future__ import annotations

import html
import re
from datetime import datetime

from ..http import Fetcher
from ..models import Posting
from ..textutil import clean_html_lines

NAME = "maxplanck"
LABEL = "Max Planck Society"
BASE = "https://www.mpg.de"
MORE = BASE + "/jobboard/115824/more_items"
#: Without this the endpoint answers the whole jobboard page instead of a fragment.
XHR = {"X-Requested-With": "XMLHttpRequest"}

_CARD = re.compile(r'<li class="teaser teaser-horizontal">(.*?)</li>', re.S)
_LINK = re.compile(r'<h3>\s*<a href="([^"]+)">([^<]+)</a>', re.S)
_DATE = re.compile(r'<span class="date">([^<]+)</span>')
_PLACE = re.compile(r"</div>\s*<div>\s*([^<]+?)\s*</div>", re.S)
_ID = re.compile(r"^/(\d{6,})/")


def _iso(text: str) -> str:
    try:
        return datetime.strptime(text.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


def _split_place(line: str) -> tuple[str, str]:
    """`Max Planck Institute for Solar System Research, Göttingen` -> name, city.

    The city is the last comma segment, because institute names contain commas of
    their own ("Max Planck Institute for the Study of Societies, Cologne").
    """
    line = " ".join(html.unescape(line).split())
    if "," not in line:
        return line, ""
    parts = [p.strip() for p in line.split(",")]
    # The board repeats the city for some institutes: "… Intelligent Systems,
    # Stuttgart, Stuttgart". The repeat is noise, not a second location.
    if len(parts) >= 3 and parts[-1].lower() == parts[-2].lower():
        parts = parts[:-1]
    return ", ".join(parts[:-1]).strip(), parts[-1]


def parse(text: str) -> list[Posting]:
    out: list[Posting] = []
    for card in _CARD.findall(text or ""):
        link = _LINK.search(card)
        if not link:
            continue
        href, title = link.group(1).strip(), " ".join(
            html.unescape(link.group(2)).split())
        if not title:
            continue
        date = _DATE.search(card)
        place = _PLACE.search(card)
        company, location = _split_place(place.group(1) if place else "")
        ident = _ID.match(href)
        out.append(Posting(
            source=NAME,
            title=title,
            company=company,
            location=location,
            url=BASE + href,
            external_id=ident.group(1) if ident else href,
            posted_at=_iso(date.group(1)) if date else "",
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    out: list[Posting] = []
    limit = max(1, int(settings.get("limit", 20)))
    pages = max(1, int(settings.get("max_pages", 5)))
    for page in range(pages):
        url = f"{MORE}?limit={limit}&offset={page * limit}&context=jobs"
        try:
            status, text = fetcher.get(url, headers=XHR)
        except Exception as e:                     # noqa: BLE001 - report, do not raise
            fetcher.errors.append(f"maxplanck: {e}")
            break
        if status != 200 or not text:
            fetcher.errors.append(f"maxplanck: HTTP {status}")
            break
        batch = parse(text)
        out.extend(batch)
        if len(batch) < limit:
            break
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    """The advert page: the prose sits between the header article and </main>."""
    try:
        status, text = fetcher.get(p.url)
    except Exception:                              # noqa: BLE001 - a teaser is fine
        return None
    if status != 200 or not text:
        return None
    start = text.find('<main class="content">')
    if start < 0:
        return None
    head_end = text.find("</article>", start)
    if head_end < 0:
        return None
    end = text.find("</main>", head_end)
    body = text[head_end + len("</article>"): end if end > 0 else len(text)]
    desc = clean_html_lines(body)
    return desc if len(desc) > 200 else None
