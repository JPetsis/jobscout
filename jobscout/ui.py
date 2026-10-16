"""Terminal styling: ANSI colors that switch off automatically for pipes,
`NO_COLOR`, or `jobscout --no-color`.

Principles:
  * color carries meaning (strong = gold, matches = cyan, inactive = dim)
  * never pad a string that already contains escape codes; pad first, color last
"""
from __future__ import annotations

import os
import sys

_ENABLED: bool | None = None


def init(force_off: bool = False) -> None:
    global _ENABLED
    _ENABLED = (not force_off) and sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def enabled() -> bool:
    if _ENABLED is None:
        init()
    return bool(_ENABLED)


def _wrap(code: str, s: str) -> str:
    return f"\x1b[{code}m{s}\x1b[0m" if enabled() else s


def bold(s: str) -> str:
    return _wrap("1", s)


def dim(s: str) -> str:
    return _wrap("2", s)


def red(s: str) -> str:
    return _wrap("31", s)


def green(s: str) -> str:
    return _wrap("32", s)


def cyan(s: str) -> str:
    return _wrap("36", s)


def magenta(s: str) -> str:
    return _wrap("35", s)


def gold(s: str) -> str:
    return _wrap("1;33", s)


def score_text(text: str, value: float, min_score: float, strong_threshold: float) -> str:
    """Color an already-padded score string by band."""
    if value >= strong_threshold:
        return gold(text)
    if value >= min_score:
        return cyan(text)
    return dim(text)


def contract_pill(label: str) -> str:
    """Colored tag for a contract marker from contract_label_from_reasons."""
    if not label:
        return ""
    family, _, term = label.partition(":")
    if family == "perm":
        return green(f"[{term}]")
    if family == "tmp":
        return dim(f"[{term}]")
    return ""


def rule(width: int = 58) -> str:
    return dim("─" * width)
