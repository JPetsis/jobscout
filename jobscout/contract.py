"""One word per fact, whatever language the advert used.

The window is English and the adverts are not. A posting can say "unbefristet",
"Festanstellung", "fixe Anstellung" or "permanent" and mean the same thing, and
the chip used to repeat whichever phrase happened to match -- so the same fact
appeared under five names, four of them German, in a window that otherwise reads
in English.

So the chip shows a canonical word, and the matched phrase stays in the score
breakdown (`perm:unbefristet`), which is where a person checks the machine's
reading. Nothing is hidden: the reason still says exactly what was matched.

This module is the one table. Two things follow from it:

  * `config.DEFAULT_CONFIG` lists the same terms, and a test keeps the two in
    step, because a term that exists in one and not the other is a term that
    silently stops matching.
  * `PERMANENT_TERMS` is the list the scorer reads through the config, so the
    regional variants below are what a fresh profile actually ships with.

Regional coverage matters here because a DACH search is not one language. The
Austrian and Swiss words for a permanent post are not the German ones, and a
list that only knows "unbefristet" quietly misses half of Vienna.
"""
from __future__ import annotations

from .textutil import norm

PERMANENT = "permanent"
FIXED_TERM = "fixed-term"

#: phrase (as an advert writes it) -> canonical word. Order is the order the
#: scorer tries them in, which only decides which phrase a reason names when a
#: posting says two of them; `permanent_cap` means the points cannot stack.
VARIANTS: dict[str, tuple[str, ...]] = {
    PERMANENT: (
        # German, used in DE, AT and CH alike.
        "unbefristet", "festanstellung", "feste anstellung", "feste stelle",
        "dauerstelle",
        # Austria and Switzerland say "fix" where Germany says "fest"; the
        # Planstelle is the permanent public-sector post (universities too).
        "fixe anstellung", "fixe stelle", "planstelle",
        # English, including the EU institutions' "contract of indefinite
        # duration" and the British "open-ended contract".
        "permanent", "open-ended", "open ended", "indefinite",
    ),
    FIXED_TERM: (
        "befristet", "zeitvertrag", "vertrag auf zeit",
        # Cover for somebody who is away: fixed-term by nature, and the German
        # and Austrian words for it are specific enough not to fire elsewhere.
        "elternzeitvertretung", "karenzvertretung", "urlaubsvertretung",
        "temporary", "fixed-term", "fixed term",
        "maternity cover", "parental leave cover",
        # Swiss German, and also used in Austrian adverts.
        "temporär",
    ),
}

PERMANENT_TERMS: tuple[str, ...] = VARIANTS[PERMANENT]
TEMPORARY_TERMS: tuple[str, ...] = VARIANTS[FIXED_TERM]

_BY_TERM: dict[str, str] = {
    norm(term): canonical
    for canonical, terms in VARIANTS.items()
    for term in terms
}


def label(term: str) -> str:
    """The canonical word for one matched phrase, or "" if it is not ours."""
    return _BY_TERM.get(norm(term).strip(), "")


def from_reasons(reasons) -> str:
    """The canonical contract word for a posting's reasons, or "".

    The prefix is the authority (`perm:` / `tmp:`), because that is what the
    scorer decided; the phrase after it is only the evidence, and it stays in
    the breakdown where a person can read it.
    """
    for item in reasons or ():
        try:
            text = str(item[0])
        except (TypeError, IndexError):
            continue
        if text.startswith("perm:"):
            return PERMANENT
        if text.startswith("tmp:"):
            return FIXED_TERM
    return ""
