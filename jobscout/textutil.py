"""Small text helpers: HTML stripping, normalization, slugs, dates."""
from __future__ import annotations

import datetime as _dt
import html
import re
from html.entities import html5 as _html5

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
_ISO_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def clean_html(s: str) -> str:
    """Strip tags (incl. script/style bodies), unescape entities, collapse whitespace."""
    if not s:
        return ""
    s = _SCRIPT.sub(" ", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    return _WS.sub(" ", s).strip()


_XML_ENT = re.compile(r"&([a-zA-Z][a-zA-Z0-9]{1,31});")
_XML_BUILTIN = {"amp", "lt", "gt", "quot", "apos"}


def xml_safe(text: str) -> str:
    """Make HTML-flavoured XML parseable by the stdlib parser.

    RSS feeds often ship HTML entities (``&rsquo;``, ``&nbsp;``) that XML does
    not define, and ElementTree raises ParseError on them. Convert those to
    numeric references (valid XML) so the text survives; ``clean_html`` decodes
    them later. Unknown entities are dropped.
    """
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name in _XML_BUILTIN:
            return m.group(0)
        ch = _html5.get(name + ";")
        return f"&#{ord(ch)};" if ch else " "

    return _XML_ENT.sub(sub, text or "")


_BLOCK_TAGS = re.compile(
    r"</?(?:p|div|li|ul|ol|tr|td|table|blockquote|section|article|pre|h[1-6])\b[^>]*>",
    re.I,
)


def clean_html_lines(s: str) -> str:
    """Like clean_html but keeps line breaks (HN comments, plain-text authors).

    HN separates paragraphs with bare ``<p>`` opening tags (no closing tag), so
    the block-tag pass must run before the generic tag strip -- otherwise the
    last word of one paragraph and the first of the next get glued together.
    """
    if not s:
        return ""
    s = _SCRIPT.sub(" ", s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = _BLOCK_TAGS.sub("\n", s)
    s = _TAG.sub("", s)
    s = html.unescape(s)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def norm(s: str) -> str:
    """Lowercase + collapsed whitespace, for matching."""
    return _WS.sub(" ", (s or "").lower()).strip()


def slug(s: str) -> str:
    """URL-ish slug: umlauts transliterated, everything else folded to dashes."""
    s = (s or "").translate(_UMLAUT).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def snippet(s: str, n: int = 220) -> str:
    s = clean_html(s)
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "\u2026"


def date_part(s: str) -> str:
    """Return the YYYY-MM-DD prefix of an ISO timestamp, else the raw string."""
    m = _ISO_DATE.match((s or "").strip())
    return m.group(1) if m else (s or "").strip()


def iso_from_epoch(value) -> str:
    """Unix seconds/ms -> YYYY-MM-DD. Tolerates junk."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v > 1e12:  # milliseconds
        v = v / 1000.0
    import datetime as _dt

    try:
        return _dt.datetime.fromtimestamp(v, _dt.timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


_DE_UNITS = {"stunde": 0, "stunden": 0, "tag": 1, "tagen": 1, "woche": 7, "wochen": 7,
             "monat": 30, "monaten": 30, "jahr": 365, "jahren": 365}
_EN_UNITS = {"hour": 0, "hours": 0, "day": 1, "days": 1, "week": 7, "weeks": 7,
             "month": 30, "months": 30, "year": 365, "years": 365}
_WORD_NUM = {"einem": 1, "einer": 1, "ein": 1, "eines": 1, "one": 1, "a": 1, "an": 1}


def de_rel_date(text: str, today=None) -> str:
    """Relative dates ('gestern', 'vor 3 Tagen', '2 days ago') -> ISO date, else ''."""
    t = (text or "").strip().lower()
    if not t:
        return ""
    today = today or _dt.date.today()
    if "heute" in t or "today" in t or "just now" in t or "gerade eben" in t:
        return today.isoformat()
    if "gestern" in t or "yesterday" in t:
        return (today - _dt.timedelta(days=1)).isoformat()
    m = re.search(r"vor\s+(\d+|[a-z\u00e4\u00f6\u00fc\u00df]+)\s+([a-z\u00e4\u00f6\u00fc]+)", t)
    if m:
        n_raw, unit = m.group(1), m.group(2)
        n = int(n_raw) if n_raw.isdigit() else _WORD_NUM.get(n_raw)
        days = _DE_UNITS.get(unit)
        if n is not None and days is not None:
            return (today - _dt.timedelta(days=n * days)).isoformat()
    m = re.search(r"(\d+)\s+([a-z]+)\s+ago", t)
    if m:
        days = _EN_UNITS.get(m.group(2))
        if days is not None:
            return (today - _dt.timedelta(days=int(m.group(1)) * days)).isoformat()
    return ""


def balanced_json(s: str, start: int):
    """Extract a balanced {...} object starting at or after `start`. Returns (obj_text, end) or None."""
    j = s.find("{", start)
    if j < 0:
        return None
    depth = 0
    instr = False
    esc = False
    for k in range(j, len(s)):
        c = s[k]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[j : k + 1], k + 1
    return None
