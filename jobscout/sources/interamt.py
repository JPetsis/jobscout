"""Interamt, the German public-sector board, where universities advertise IT staff.

Its search is a Wicket form, not a query string: `trefferliste?suchbegriffe=...`
and every other spelling returned the same 11,111 offers, because the parameter
was ignored. The working path is to load the quick-search form, post
`idOrSuchtext` with the hidden fields it carries, and follow the redirect. The
page instance lives in the URL and the session, so each query gets its own GET.

The result table is server-rendered with one row per advert and ten rows a page.
Paging is another Wicket callback and is not followed: each query reads its
first page, which for a specific role is the whole answer (16 offers for
"Softwareentwickler"). Detail pages have a stable address,
/koop/app/stellenangebot/<id>, so the link stored is that one, not the
session-bound callback in the table.
"""
from __future__ import annotations

import html
import re
import time
import urllib.parse
import urllib.request
import http.cookiejar

from ..http import default_ssl_context
from ..models import Posting

NAME = "interamt"
LABEL = "Interamt"
BASE = "https://interamt.de"
START = BASE + "/koop/app/"

_RE_ROW = re.compile(r'<tr class="ia-e-table__row"')
_RE_TD = re.compile(r'<td[^>]*data-field="([^"]+)"[^>]*>(.*?)</td>', re.S)
_RE_TD_ANY = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_RE_HREF = re.compile(r'href="([^"]+)"')


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _description(page: str) -> str:
    """The advert's own words, whole.

    Interamt puts the body in a `ia-e-richtext` block: paragraphs, headings and
    lists. Reading only the `<p>` tags loses every bullet, which on a German
    public-sector advert is most of the requirements -- the Pforzheim advert has
    51 list items and almost no paragraphs.
    """
    i = page.find('class="ia-e-richtext')
    if i == -1:
        return ""
    start = page.find(">", i) + 1
    end = page.find("ia-e-section-title", start)
    chunk = page[start:end if end != -1 else start + 60000]
    chunk = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", chunk)
    chunk = re.sub(r"(?i)<li[^>]*>", "\n\u2022 ", chunk)
    chunk = re.sub(r"(?i)</?(?:p|div|h[1-6]|tr|ul|ol|table|section)[^>]*>", "\n", chunk)
    chunk = re.sub(r"(?i)<br\s*/?>", "\n", chunk)
    text = html.unescape(re.sub(r"<[^>]+>", " ", chunk))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _iso(day_month_year: str) -> str:
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", day_month_year)
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else ""


def parse_results(page: str) -> list[Posting]:
    """The postings in one Interamt result page."""
    out: list[Posting] = []
    for chunk in _RE_ROW.split(page)[1:]:
        row = chunk.split("</tr>", 1)[0]
        cells = {k: _text(v) for k, v in _RE_TD.findall(row)}
        if not cells:
            plain = [_text(c) for c in _RE_TD_ANY.findall(row)]
            if len(plain) < 10:
                continue
            cells = dict(zip(("StellenangebotId", "Behoerde", "Stellenbezeichnung",
                              "BesoldungGruppeDisplayString", "TarifEbeneDisplayString",
                              "PLZOrte", "EntfernungString", "Dienstort", "Von",
                              "Bewerbungsfrist"), plain))
        ident = cells.get("StellenangebotId", "")
        m = re.search(r"\b(\d{5,})\b", ident)
        title = cells.get("Stellenbezeichnung", "")
        if not m or not title:
            continue
        out.append(Posting(
            source=NAME,
            title=title,
            company=cells.get("Behoerde", ""),
            location=cells.get("PLZOrte", ""),
            # the only address that renders the advert without a live search
            # session: it redirects once (?0&id=) and then serves the page. The
            # table's own href is a session-bound Wicket callback.
            url=f"{BASE}/koop/app/stelle?id={m.group(1)}",
            external_id=m.group(1),
            posted_at=_iso(cells.get("Von", "")),
            expires_at=_iso(cells.get("Bewerbungsfrist", "")),
            compensation=cells.get("TarifEbeneDisplayString", ""),
        ))
    return out


def _search(keywords: str, opener, delay: float) -> str:
    """POST the quick-search form and return the result page HTML."""
    start = opener.open(START, timeout=30).read().decode("utf-8", "replace")
    block = next((m.group(0) for m in re.finditer(r"<form[^>]*>.*?</form>", start, re.S)
                  if "idOrSuchtext" in m.group(0)), "")
    if not block:
        raise RuntimeError("interamt: the quick-search form is gone from the start page")
    found = re.search(r'action="([^"]+)"', block)
    if not found:
        raise RuntimeError("interamt: the quick-search form has no action")
    action = urllib.parse.urljoin(START, found.group(1).replace("&amp;", "&"))
    data = {"idOrSuchtext": keywords,
            "navFooter:navFooter_body:submitRow:actions:0:button": ""}
    for h in re.findall(r'<input[^>]*type="hidden"[^>]*>', block):
        name = re.search(r'name="([^"]*)"', h)
        value = re.search(r'value="([^"]*)"', h)
        if name:
            data[name.group(1)] = (value.group(1) if value else "").replace("&amp;", "&")
    time.sleep(delay)
    req = urllib.request.Request(action, data=urllib.parse.urlencode(data).encode(),
                                 method="POST")
    return opener.open(req, timeout=40).read().decode("utf-8", "replace")


def fetch(settings: dict, fetcher, *, delay: float | None = None) -> list[Posting]:
    """One page per query, in the order the queries are written."""
    queries = settings.get("queries") or []
    pace = float(delay if delay is not None else settings.get("host_delay", 1.0) or 1.0)
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=default_ssl_context()),
        urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", fetcher.user_agent if hasattr(fetcher, "user_agent")
                          else "Mozilla/5.0 (compatible; jobscout)"),
                         ("Accept-Language", "de-DE,de;q=0.9")]
    if not queries:
        # Enabled but unasked: without this, the board quietly returns nothing
        # and the health table reads "nothing" forever, which looks like a
        # broken parser rather than a profile that never told it what to search.
        fetcher.errors.append(f"{NAME}: no queries configured for this profile")
        return []

    out: list[Posting] = []
    seen: set[str] = set()
    for q in queries:
        keywords = (q.get("keywords") if isinstance(q, dict) else str(q)) or ""
        if not keywords:
            continue
        try:
            page = _search(keywords, opener, pace)
        except Exception as exc:                    # noqa: BLE001 - report, do not die
            fetcher.errors.append(f"{NAME}: {keywords}: {exc}")
            continue
        rows = parse_results(page)
        limit = int(settings.get("detail_limit", 12) or 0)
        before = len(out)
        for p in rows:
            if p.external_id in seen:
                continue
            seen.add(p.external_id)
            # Scoring needs the advert's words, and with a requirement filter on
            # a posting with no text is filtered out unseen. Fetch the detail in
            # the same session, but only for the first page of a query.
            if limit and (len(out) - before) < limit:
                try:
                    time.sleep(pace)
                    detail = opener.open(p.url, timeout=40).read().decode("utf-8", "replace")
                    p.description = _description(detail)
                except Exception as exc:                # noqa: BLE001
                    fetcher.errors.append(f"{NAME}: detail {p.external_id}: {exc}")
            out.append(p)
    return out
