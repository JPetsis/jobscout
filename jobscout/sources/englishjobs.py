"""English Jobs (Germany) — English-speaking jobs in Germany.

Category pages: `GET /jobs/<category>?page=N` (`remote`, `visa-sponsorship`,
`part-time`, ...). The site also serves the same listing as
`?format=markdown`; HTML is parsed here because the markdown variant
truncates long signed URLs in display.

Job links are SIGNED `/clickout/<hash>` redirects that answer 302 to the
publisher's own page (seen live: stepstone.de and an employer ATS), so the
stored URL is the absolute clickout link (browsers follow it fine, and
liveness probes ride the redirect). Consequence for the store: a resolved
target can duplicate a row from another source, and the project's
company+title dedup is the guard. Cards: `h3` title, two `</svg> text </li>`
meta lines (company, city), and the card carries `id="<hash>"`, which is
stored as `external_id`.
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote

from ..http import Fetcher
from ..models import Posting

NAME = "englishjobs"
LABEL = "English Jobs (Germany)"
BASE = "https://englishjobs.de"

_CARD = re.compile(r'<div id="([0-9a-f]{8,})"[^>]*class="job js-job')
_TITLE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
_LINK = re.compile(r'href="(/clickout/[^"]+)"')
_META = re.compile(r"</svg>\s*([^<]+?)\s*</li>")


def parse(text: str) -> list[Posting]:
    text = text or ""
    cards = list(_CARD.finditer(text))
    out = []
    for i, m in enumerate(cards):
        start = m.start()
        end = cards[i + 1].start() if i + 1 < len(cards) else len(text)
        chunk = text[start:end]
        link = _LINK.search(chunk)
        t = _TITLE.search(chunk)
        if not link or not t:
            continue
        title = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t.group(1)))).strip()
        if not title:
            continue
        metas = _META.findall(chunk)
        out.append(Posting(
            source=NAME,
            title=title,
            company=(unescape(metas[0]).strip() if metas else ""),
            location=(unescape(metas[1]).strip() if len(metas) > 1 else ""),
            url=BASE + unescape(link.group(1)),
            external_id=m.group(1),
        ))
    return out


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    cats = settings.get("categories") or ["remote"]
    pages = max(1, int(settings.get("pages", 1) or 1))
    out: list[Posting] = []
    for cat in cats:
        cat = str(cat).strip().strip("/")
        if not cat:
            continue
        for page in range(1, pages + 1):
            url = f"{BASE}/jobs/{quote(cat)}"
            if page > 1:
                url += f"?page={page}"
            try:
                status, text = fetcher.get(url)
            except Exception as e:
                fetcher.errors.append(f"{NAME}/{cat}: {e}")
                break
            if status != 200:
                fetcher.errors.append(f"{NAME}/{cat}: HTTP {status}")
                break
            batch = parse(text)
            out.extend(batch)
            if not batch:
                break
    return out


def fetch_detail(p: Posting, fetcher: Fetcher) -> str | None:
    from .. import detail as detailmod
    return detailmod.html_detail(p.url, fetcher)
