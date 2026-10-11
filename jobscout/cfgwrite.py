"""Surgical, comment-preserving edits to config.yaml.

WHY THIS EXISTS — the constraint that shapes everything here:
    yaml.safe_load(config)  ->  yaml.safe_dump(...)
leaves every VALUE correct and deletes ALL 137 comment lines from the live
config. It looks successful. The config is a documented file and those comments
are the product, so a settings UI must never round-trip it.

So every function below is a line-level text edit: it locates the exact line
span for one key and rewrites only that span. Everything else — comments,
ordering, blank lines, quoting style — is preserved byte for byte.

`companies._inject()` already does this for one specific case (board tokens);
this generalises the same discipline to scalars, lists and mappings.

Deliberate behaviours:
  * an unknown key RAISES. It never appends, because appending a key the user
    did not ask for is how a hand-maintained file gets mangled.
  * writing the same value back is a byte-identical no-op, which is the
    safety property the tests assert against a copy of the real config.
"""
from __future__ import annotations

from pathlib import Path

INDENT = 2


class ConfigEditError(Exception):
    """Raised when a key cannot be located, or a value cannot be represented."""


# ---------------------------------------------------------------- scanning

def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_blank_or_comment(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


def _key_of(line: str) -> str | None:
    """The key on a `key:` / `key: value` line, or None."""
    s = line.strip()
    if not s or s.startswith("#") or s.startswith("-"):
        return None
    if ":" not in s:
        return None
    return s.split(":", 1)[0].strip()


def _block_end(lines: list[str], key_idx: int, indent: int) -> int:
    """Index just past the last line belonging to the block opened at key_idx.

    A line belongs to the block while it is blank/comment, or indented deeper
    than the key itself.
    """
    i = key_idx + 1
    last = key_idx + 1
    while i < len(lines):
        ln = lines[i]
        if _is_blank_or_comment(ln):
            i += 1
            continue
        if _indent_of(ln) > indent:
            last = i + 1
            i += 1
            continue
        break
    return last


def _find_key(lines: list[str], key: str, lo: int, hi: int, indent: int) -> int | None:
    for i in range(lo, min(hi, len(lines))):
        ln = lines[i]
        if _is_blank_or_comment(ln):
            continue
        if _indent_of(ln) == indent and _key_of(ln) == key:
            return i
    return None


def _locate(lines: list[str], dotted: str) -> tuple[int, int, int, int]:
    """Resolve a dotted path.

    Returns (key_idx, span_end, indent, parent_indent). `span_end` is exclusive
    and covers the key's own block, so a list/mapping can be replaced wholesale.
    """
    path = [p for p in dotted.split(".") if p]
    if not path:
        raise ConfigEditError("empty key path")

    lo, hi, indent = 0, len(lines), 0
    for seg in path[:-1]:
        idx = _find_key(lines, seg, lo, hi, indent)
        if idx is None:
            raise ConfigEditError(f"key not found: {'.'.join(path)} (missing {seg!r})")
        lo, hi, indent = idx + 1, _block_end(lines, idx, indent), indent + INDENT

    key = path[-1]
    idx = _find_key(lines, key, lo, hi, indent)
    if idx is None:
        raise ConfigEditError(
            f"key not found: {dotted!r}. Refusing to append a new key — "
            "jobscout never invents config entries.")
    return idx, _block_end(lines, idx, indent), indent, indent - INDENT


# ---------------------------------------------------------------- emitting

def _split_trailing_comment(rest: str) -> tuple[str, str]:
    """Split ` 6            # note` into ('6', '            # note').

    The returned comment KEEPS its leading whitespace on purpose: rebuilding the
    line as `value` + comment then reproduces the original spacing exactly. If
    the gap were dropped the line would become `min_score: 7# note`, where the
    `#` is no longer a comment and the value silently becomes `7# note`.

    A `#` opens a YAML comment only outside quotes and after whitespace, which
    matters because real values here contain URLs and `ci/cd`.
    """
    in_single = in_double = False
    for i, ch in enumerate(rest):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or rest[i - 1] in " \t":
                val = rest[:i].rstrip()
                return val, rest[len(val):]
    return rest.rstrip(), ""


def _scalar_line(line: str, key: str, value) -> str:
    """Rebuild one `key: value` line, PRESERVING any inline trailing comment.

    `min_score: 6   # below this ...` must stay that way after an edit; losing
    the trailing comment is the same class of loss as losing a whole line, and
    it is silent.
    """
    rest = line.split(":", 1)[1]
    body = rest.rstrip("\n")
    nl = "\n" if rest.endswith("\n") else ""
    old_val, comment = _split_trailing_comment(body)
    if _strip_quotes(old_val.strip()) == value:            # true no-op
        return line
    new = f"{' ' * _indent_of(line)}{key}: {_emit_scalar(value)}"
    if comment:
        new += comment                                     # keeps its own spacing
    return new + nl


def _emit_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    s = str(value)
    if s == "" or any(c in s for c in ":#[]{}\"'\n") or s.strip() != s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _emit_inline_list(values) -> str:
    return "[" + ", ".join(_emit_scalar(v) for v in values) + "]"


# ---------------------------------------------------------------- reading

def get_value(text: str, dotted: str):
    """Best-effort read of one key. Returns None when absent (never raises)."""
    lines = text.splitlines()
    try:
        idx, span_end, indent, _ = _locate(lines, dotted)
    except ConfigEditError:
        return None
    raw = lines[idx].split(":", 1)[1].strip()
    if raw:
        return _parse_inline(raw)
    items = [ln.strip()[2:].strip() for ln in lines[idx + 1:span_end]
             if ln.strip().startswith("- ")]
    if items:
        return [_parse_inline(i) for i in items]
    sub = {}
    for ln in lines[idx + 1:span_end]:
        k = _key_of(ln)
        if k and _indent_of(ln) == indent + INDENT:
            sub[k] = _parse_inline(ln.split(":", 1)[1].strip())
    return sub or None


def _parse_inline(raw: str):
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(x.strip()) for x in inner.split(",") if x.strip()]
    return _strip_quotes(raw)


def _strip_quotes(s: str):
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s in ("true", "false"):
        return s == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


# ---------------------------------------------------------------- writing

def set_scalar(text: str, dotted: str, value) -> str:
    """Set one scalar. Only that key's line changes, trailing comment included."""
    lines = text.splitlines(keepends=True)
    idx, _span, _indent, _pind = _locate(lines, dotted)
    lines[idx] = _scalar_line(lines[idx], _key_of(lines[idx]), value)
    return "".join(lines)


def set_list(text: str, dotted: str, values) -> str:
    """Set a list, whichever style it already uses.

    An inline `key: [a, b]` stays inline (a settings screen should not reformat
    a file it was only asked to change one value in). A block list stays block.
    Both keep any inline trailing comment on the key line.
    """
    lines = text.splitlines(keepends=True)
    idx, span_end, indent, _pind = _locate(lines, dotted)
    key = _key_of(lines[idx])
    nl = "\n" if lines[idx].endswith("\n") else ""
    lead = " " * indent
    raw = lines[idx].split(":", 1)[1]
    body = raw.rstrip("\n")
    old_val, comment = _split_trailing_comment(body)

    if old_val.strip().startswith("["):                       # inline flow list
        if _parse_inline(old_val.strip()) == list(values):    # true no-op
            return text
        new = f"{lead}{key}: {_emit_inline_list(values)}"
        return "".join(lines[:idx] + [new + (comment or "") + nl] + lines[idx + 1:])

    block = [f"{lead}  - {_emit_scalar(v)}\n" for v in values]
    if not block:
        block = [f"{lead}  []\n"]
    head = f"{lead}{key}:"
    if comment:
        head += " " + comment.lstrip()
    head += nl
    # keep comments that sat under the old items
    tail = [ln for ln in lines[idx + 1:span_end] if _is_blank_or_comment(ln)]
    return "".join(lines[:idx] + [head] + block + tail + lines[span_end:])


def set_mapping(text: str, dotted: str, mapping: dict) -> str:
    """Replace a `key:` mapping block, keeping its trailing comment and any
    comments that sat inside the block."""
    lines = text.splitlines(keepends=True)
    idx, span_end, indent, _pind = _locate(lines, dotted)
    key = _key_of(lines[idx])
    nl = "\n" if lines[idx].endswith("\n") else ""
    lead = " " * indent
    _old, comment = _split_trailing_comment(lines[idx].split(":", 1)[1].rstrip("\n"))

    current = get_value(text, dotted)
    if current == mapping:                                    # true no-op
        return text

    head = f"{lead}{key}:"
    if comment:
        head += " " + comment.lstrip()
    head += nl
    block = [head]
    for k, v in mapping.items():
        block.append(f"{lead}  {_emit_scalar(k)}: {_emit_scalar(v)}{nl}")
    tail = [ln for ln in lines[idx + 1:span_end] if _is_blank_or_comment(ln)]
    return "".join(lines[:idx] + block + tail + lines[span_end:])


# ---------------------------------------------------------------- file API

def _read(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def edit(path: Path, op: str, dotted: str, value) -> bool:
    """Apply one edit to a file. Returns True when the file changed.

    A no-op (same value already present) leaves the file byte-identical.
    """
    path = Path(path)
    before = _read(path)
    fn = {"scalar": set_scalar, "list": set_list, "mapping": set_mapping}.get(op)
    if fn is None:
        raise ConfigEditError(f"unknown op {op!r}")
    after = fn(before, dotted, value)
    if after != before:
        path.write_text(after, encoding="utf-8")
        return True
    return False
