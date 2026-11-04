"""Requirements an advert states that keywords cannot catch.

The filters see words: a title, a stack, a country. Some of the things that
decide whether an advert is worth an evening are sentences instead — "sehr gute
Deutschkenntnisse (C1)" on an English-language advert, "PhD required", "must be
eligible to work in the EU", "at least 5 years".

This module reads those sentences and returns short, checkable labels. It is
deliberately literal: every label corresponds to text that is actually in the
advert, because a wrong "PhD required" chip would make someone skip a job they
could have had. Silence means "not stated", never "not required".

Attaching a level to a language is the hard part. Order alone is not enough
("Sehr gute Deutschkenntnisse (C1) und gute Englischkenntnisse" has two
languages and one explicit level, and the C1 is German), so the sentence is split
into clauses and a level stays inside its own clause. A trailing level with no
language in its clause ("… (mindestens C1)") belongs to the clause before it.
"""
from __future__ import annotations

import re

_LANG = re.compile(r"\b(deutsch|german|englisch|english)\w*", re.I)
_LEVEL = re.compile(
    r"\b(a1|a2|b1|b2|c1|c2|flie(?:ß|ss)end|fluen(?:t|cy)|sehr gut|very good|gute|good)\b",
    re.I)
_LANG_NAME = {"deutsch": "German", "german": "German",
              "englisch": "English", "english": "English"}
#: Ordering only, for choosing one label per language.
_RANK = {"a1": 1, "a2": 2, "b1": 3, "b2": 4, "c1": 5, "c2": 6,
         "fluent": 5, "very good": 5, "good": 4}
_CLAUSE = re.compile(r"[;:()]|,|\bund\b|\band\b|/", re.I)

_PHD = re.compile(r"\b(ph\.?d\.?|doktorwürde|promotion|doctorate)\b", re.I)
_PERMIT = re.compile(
    r"\b(arbeitserlaubnis|aufenthaltstitel|arbeitsberechtigt|work permit|"
    r"right to work|eligible to work|work authorisation|work authorization)\b", re.I)
_YEARS = re.compile(
    r"\b(?:mindestens|at least|min\.?|minimum(?: of)?)\s+(\d{1,2})\s*\+?\s*"
    r"(?:jahre|years?)\b", re.I)
_YEARS_PLUS = re.compile(r"\b(\d{1,2})\s*\+\s*(?:jahre|years?)\b", re.I)

#: AI is asked for in three ways and only one of them is a requirement.
_AI = re.compile(
    r"\b(?:ki|ai|llms?|genai|generative(?:n)? ki|k\u00fcnstliche intelligenz|"
    r"artificial intelligence|machine learning|deep learning|"
    r"prompt engineering|agentic|rag)\b", re.I)
_AI_NEED = re.compile(
    r"\b(?:voraussetzung|zwingend|erforderlich|erwartet|erwarten|"
    r"m\u00fcssen|m\u00fcssen sie|required|requirement|must|erfahrung mit|"
    r"umgang mit|kenntnisse (?:in|mit)|vertraut|sicher im umgang|"
    r"nachweislich|hands-on|praktische erfahrung)\b", re.I)
_AI_BONUS = re.compile(
    r"\b(?:von vorteil|w\u00fcnschenswert|wunschenswert|idealerweise|"
    r"nice to have|bonus|optional|gerne|bereicherung|"
    r"not required|nicht erforderlich)\b", re.I)

#: The nearest language must be within this many characters of the level.
_MAX_GAP = 60


def ai_signal(text: str) -> str:
    """How an advert asks for AI work: 'required', 'bonus' or ''.

    Only a requirement sentence counts. "Erfahrung mit KI von Vorteil" mentions
    AI and is exactly the advert a requirement filter is meant to exclude, so
    the bonus marker in the same sentence wins. Silence means not stated: many
    good adverts describe the work without naming it, and guessing there would
    hide jobs the person could have had.
    """
    # Clause and proximity, not sentence membership: a bulleted requirement list
    # is one "sentence" by newline, and "Erfahrung mit ..." three bullets above
    # the word "KI" would otherwise make every such advert a requirement.
    kinds = [_ai_kind_at(text, m) for m in _AI.finditer(text or "")]
    if "required" in kinds:
        return "required"
    return "bonus" if "bonus" in kinds else ""


def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """The clause containing a match, as offsets into `text`."""
    left = 0
    for m in _CLAUSE.finditer(text, 0, start):
        left = m.end()
    tail = _CLAUSE.search(text, end)
    return left, (tail.start() if tail else len(text))


def _near(clause: str, s: int, e: int, pattern: re.Pattern,
          gap: int = 45) -> bool:
    """Is a marker within `gap` characters of the term at [s, e)?"""
    for m in pattern.finditer(clause):
        if m.end() <= s and s - m.end() <= gap:
            return True
        if m.start() >= e and m.start() - e <= gap:
            return True
    return False


def _ai_kind_at(text: str, m: re.Match) -> str:
    """'bonus', 'required' or '' for one AI mention, judged in its clause."""
    a, b = _clause_bounds(text, m.start(), m.end())
    clause, s, e = text[a:b], m.start() - a, m.end() - a
    if _near(clause, s, e, _AI_BONUS):
        return "bonus"
    if _near(clause, s, e, _AI_NEED):
        return "required"
    return ""


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _clean(level: str) -> str:
    """The advert's word for the level, in a form a chip can show."""
    level = " ".join(level.strip().lower().split())
    words = {"fliessend": "fluent", "fließend": "fluent", "fluency": "fluent",
             "fluent": "fluent", "sehr gut": "very good", "very good": "very good",
             "gute": "good", "good": "good"}
    return words.get(level, level.upper())


def _languages_in(clause: str) -> list[tuple[int, int, str]]:
    """(start, end, name). The END matters: "Deutschkenntnisse" is a long word,
    and measuring from its start puts a level written right after it nearer the
    next language's start than its own."""
    return [(m.start(), m.end(), _LANG_NAME[m.group(1).lower()])
            for m in _LANG.finditer(clause)]


def _levels_in(clause: str) -> list[tuple[int, str]]:
    return [(m.start(), _clean(m.group(1))) for m in _LEVEL.finditer(clause)]


def _distance(span: tuple[int, int, str], pos: int) -> int:
    start, end, _ = span
    if start <= pos <= end:
        return 0
    return min(abs(pos - start), abs(pos - end))


def _labels_for_sentence(sentence: str) -> list[tuple[str, str, str]]:
    """[(language, level, evidence)] with one label per language, the best level."""
    best: dict[str, tuple[int, str, str]] = {}
    previous: list[tuple[int, int, str]] = []
    for clause in _CLAUSE.split(sentence):
        if not clause.strip():
            continue
        langs = _languages_in(clause)
        levels = _levels_in(clause)
        targets = langs or (previous if levels else [])
        for pos, level in levels:
            if not targets:
                continue
            near = min(targets, key=lambda span: _distance(span, pos))
            if _distance(near, pos) > _MAX_GAP:
                continue
            rank = _RANK.get(level.lower(), 0)
            held = best.get(near[2])
            if held is None or rank > held[0]:
                best[near[2]] = (rank, level, sentence.strip())
        if langs:
            previous = langs
    return [(name, level, why) for name, (_r, level, why) in best.items()]


def extract(text: str) -> list[dict]:
    """Short labels for the requirements this text states, deduplicated.

    Each is {"kind": "language" | "degree" | "permit" | "experience",
             "label": "German C1", "detail": "the sentence it came from"}.
    """
    found: list[dict] = []
    if ai_signal(text) == "required":
        m = next((m for m in _AI.finditer(text)
                  if _ai_kind_at(text, m) == "required"), None)
        detail = ""
        if m:
            a, b = _clause_bounds(text, m.start(), m.end())
            detail = text[a:b].strip()
        found.append({"kind": "ai", "label": "AI required", "detail": detail})
    seen: set[tuple[str, str]] = set()

    def add(kind: str, label: str, detail: str) -> None:
        if (kind, label) in seen:
            return
        seen.add((kind, label))
        found.append({"kind": kind, "label": label, "detail": detail.strip()[:200]})

    for sentence in _sentences(text):
        for name, level, why in _labels_for_sentence(sentence):
            add("language", f"{name} {level}", why)
        if _PHD.search(sentence):
            add("degree", "PhD / doctorate", sentence)
        if _PERMIT.search(sentence):
            add("permit", "Work permit / right to work", sentence)
        years = _YEARS.search(sentence) or _YEARS_PLUS.search(sentence)
        if years:
            add("experience", f"{years.group(1)}+ years", sentence)
    return found
