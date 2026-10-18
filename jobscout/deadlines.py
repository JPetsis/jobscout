"""Application deadlines, read out of the advert text.

Why this is fiddly enough to deserve its own module: an advert is full of dates,
and almost none of them are deadlines. A first pass over this store found 290
postings "mentioning a deadline" and 41 with a date near it, and reading those
41 showed most were start dates ("Starting on January 1, 2027") or application
windows rather than a closing date.

So the rule here is deliberately narrow: a DATE counts only when a deadline
WORD sits right next to it, in the same sentence-ish span, and only when it
lands in a window that a real deadline could be in. Everything else is ignored,
because a wrong deadline is worse than none -- somebody plans an application
around it.

The phrase that matched is kept, so the window can show its work instead of
asking to be believed.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

# "15.11.2026", "15.11.26", "15.11." -- the German way, dots and all
_D_DOT = r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{2,4})?"
# "2026-11-15"
_D_ISO = r"(\d{4})-(\d{2})-(\d{2})"
# "15 November 2026", "15th of November", "15. November 2026"
_D_TEXT = (r"(\d{1,2})(?:st|nd|rd|th)?\.?\s*(?:of\s+)?(Januar|Februar|März|April|Mai|Juni|Juli|August|"
           r"September|Oktober|November|Dezember|January|February|March|April|May|June|"
           r"July|August|September|October|November|December)\.?\s*,?\s*(\d{4})?")
# "November 15, 2026". The (?!\d) matters: without it, "November 20" matches inside
# "30th November 2026" -- the day eats the first two digits of the year.
_D_TEXT_US = (r"(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|"
              r"November|Dezember|January|February|March|April|May|June|July|August|"
              r"September|October|November|December)\.?\s+(\d{1,2})(?!\d)(?:st|nd|rd|th)?,?\s*(\d{4})?")

_DATE = rf"(?:{_D_DOT}|{_D_ISO}|{_D_TEXT}|{_D_TEXT_US})"

# The words that turn a date into a deadline, German and English. Kept tight on
# purpose: "start" / "Beginn" / "zum" are NOT here, and that is the whole point.
_WORDS = (r"bewerbungs(?:frist|schluss|ende)|bewerbungen\s+(?:bis|bis\s+zum)|"
          r"bewerben\s+sie\s+sich\s+(?:bitte\s+)?(?:bis|bis\s+zum)?\s*(?:einschließlich)?|"
          r"einsendeschluss|bewerbungsdeadline|frist\s+(?:bis|endet|zum)|"
          r"(?:applications?|apply)\s+(?:close[sd]?|closing|by|before|until|due)|"
          r"closing\s+date|application\s+deadline|deadline\s+for\s+applications|"
          r"applications?\s+(?:are\s+)?(?:accepted|considered|received)\s+until|"
          r"applications?\s+(?:are\s+)?(?:accepted|considered)\s+by|"
          r"bewerbungen\s+(?:werden\s+)?(?:bis|erwartet\s+bis)")

# A deadline word, an optional connector, then a date. The window is what keeps
# a stray "Frist" from latching onto a date three paragraphs later.
_NEAR = 60
MONTHS = {"januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
          "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
          "november": 11, "dezember": 12, "january": 1, "february": 2, "march": 3,
          "may": 5, "june": 6, "july": 7, "october": 10, "december": 12}

# How far a real deadline can be from today before we call it a mis-parse.
MAX_PAST_DAYS = 400
MAX_FUTURE_DAYS = 730


def _mk(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_date(matched: str) -> date | None:
    """The matched TEXT as a date, trying each written form in turn.

    Parsing the matched text rather than the capture groups is the fix for a
    real bug: "Apply by 2026-11-15" captured ("2026","11","15"), which the
    day/month branches then read as day 2026 of month 11 and threw away. The
    groups differ per alternative; the text does not.
    """
    t = re.sub(r"\s+", " ", (matched or "").strip())
    # Two passes, because the trailing dot means opposite things: it is part of
    # "30.11." and it is punctuation after "November 30, 2026." Stripping it
    # first broke the German form; not stripping it broke the English one.
    for candidate in (t, t.rstrip(",.;")):
        got = _parse_one(candidate)
        if got is not None:
            return got
    return None


def _parse_one(t: str) -> date | None:
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = re.fullmatch(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{2,4})?", t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = m.group(3)
        year = (2000 + int(year)) if year and len(year) == 2 else (int(year) if year else None)
        return _with_year(day, month, year)

    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\.?\s*(?:of\s+)?([A-Za-zÄÖÜäöüß]+)\.?\s*,?\s*(\d{4})?", t)
    if m and m.group(2).lower().strip(".") in MONTHS:
        return _with_year(int(m.group(1)), MONTHS[m.group(2).lower().strip(".")],
                          int(m.group(3)) if m.group(3) else None)

    m = re.fullmatch(r"([A-Za-zÄÖÜäöüß]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})?", t)
    if m and m.group(1).lower().strip(".") in MONTHS:
        return _with_year(int(m.group(2)), MONTHS[m.group(1).lower().strip(".")],
                          int(m.group(3)) if m.group(3) else None)
    return None


def _with_year(day: int, month: int, year: int | None) -> date | None:
    """No year written down means the next time that day comes round."""
    if not day or not month:
        return None
    if year is None:
        today = date.today()
        year = today.year
        got = _mk(year, month, day)
        if got and got < today - timedelta(days=30):
            year += 1
    return _mk(year, month, day)


def extract(text: str, *, today: date | None = None) -> tuple[str, str] | None:
    """(deadline, the phrase it came from) or None.

    The window check is what stops the two failure modes seen on real text: a
    start date read as a deadline, and a date from a different part of the page
    attached to a Frist mentioned in passing.
    """
    if not text:
        return None
    today = today or date.today()
    flat = re.sub(r"\s+", " ", text)
    word = re.compile(_WORDS, re.I)
    date_re = re.compile(_DATE, re.I)
    for m in word.finditer(flat):
        tail = flat[m.end():m.end() + _NEAR]
        dm = date_re.search(tail)
        if not dm:
            continue
        got = _parse_date(dm.group(0))
        if got is None:
            continue
        delta = (got - today).days
        if delta < -MAX_PAST_DAYS or delta > MAX_FUTURE_DAYS:
            continue
        # The date match is found in the TAIL, so its offsets are relative to
        # the tail. Slicing `flat` with them produced empty phrases: the wording
        # is what the window shows as proof, so it has to be the real span.
        phrase = (flat[m.start():m.end()] + " " + tail[:dm.end()]).strip()
        return got.isoformat(), phrase[:200]
    return None


def days_until(deadline_at: str, *, today: date | None = None) -> int | None:
    """Whole days from today to a deadline date, or None if it has no date.

    LOCAL dates on purpose. A closing date is a calendar date on the applicant's
    wall, not a UTC instant: between midnight and 2am in Berlin, UTC is still
    yesterday, and the card would say "closes in 4 days" about something three
    days away -- and "closes today" about a deadline the user has already lost.
    Everything that counts or words a deadline goes through here, so the number
    and the sentence can never disagree about which day it is.
    """
    try:
        when = date.fromisoformat(str(deadline_at)[:10])
    except (TypeError, ValueError):
        return None
    return (when - (today or date.today())).days


def describe(deadline_at: str, *, today: date | None = None) -> str:
    """How to say it on a card: "closes today", "closes in 3 days", "closes 15 Nov"."""
    if not deadline_at:
        return ""
    try:
        when = date.fromisoformat(str(deadline_at)[:10])
    except ValueError:
        return ""
    days = days_until(deadline_at, today=today)
    if days is None:
        return ""
    if days < 0:
        return "closed " + ("yesterday" if days == -1 else f"{-days} days ago")
    if days == 0:
        return "closes today"
    if days == 1:
        return "closes tomorrow"
    if days <= 14:
        return f"closes in {days} days"
    return "closes " + when.strftime("%d %b")
