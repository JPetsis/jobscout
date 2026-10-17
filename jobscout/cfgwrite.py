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

import os
import tempfile
import threading
import time
from pathlib import Path

INDENT = 2

# Config edits are read-modify-write, and the window can fire several at once
# (ticking a board, or a settings control saving per keystroke). Two hazards,
# both of which corrupted a real config:
#   1. interleaved writers lose each other's edits;
#   2. `write_text` TRUNCATES before writing, so a concurrent read can see a
#      half-written file and derive a replacement from it, which is how
#      fragments of unrelated sections ended up duplicated in the file.
# A lock removes the first; writing to a temp file and os.replace removes the
# second, because rename is atomic and a reader sees either the old file or the
# new one, never a partial one.
_LOCK = threading.RLock()


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


def _flow_span(lines: list[str], idx: int) -> int | None:
    """If the key on line `idx` opens a flow sequence that does NOT close on the
    same line, return the index of the line that closes it.

    This matters because `sources.enabled` is written wrapped across three
    lines for readability, and treating it as one line corrupts the file: the
    replacement lands on the first line and the continuation lines are left
    dangling, which is invalid YAML. Returns None for a single-line value, an
    unterminated sequence, or anything that is not a flow sequence.
    """
    raw = lines[idx].split(":", 1)[1].rstrip("\n")
    body, _comment = _split_trailing_comment(raw)
    if "[" not in body or "]" in body:
        return None
    depth = body.count("[") - body.count("]")
    for j in range(idx + 1, len(lines)):
        stripped, _ = _split_trailing_comment(lines[j].rstrip("\n"))
        depth += stripped.count("[") - stripped.count("]")
        if depth <= 0:
            return j
    return None                                  # never closed; leave it alone


def _join_flow(lines: list[str], idx: int, end: int) -> str:
    """The full text of a flow value spanning idx..end, comments removed.

    Comments are stripped PER LINE and only then joined. Stripping after
    joining looks equivalent and is not: a `#` on the first line would swallow
    every continuation line, so
        enabled: [a, b,   # why not
                  c]
    would read back as '[a, b,' and `c` would vanish.
    """
    parts = []
    for j in range(idx, end + 1):
        body, _comment = _split_trailing_comment(lines[j].rstrip("\n"))
        parts.append(body)
    joined = "".join(parts)
    return joined.split(":", 1)[1].strip()


def _interior_comments(lines: list[str], idx: int, end: int) -> list[str]:
    """Comment text sitting INSIDE a wrapped flow value.

    A comment between the brackets cannot be kept where it is (the value is
    rewritten wholesale), so it is lifted out and re-emitted above the key
    rather than dropped: config comments are documentation, and losing one
    silently is the exact failure this module exists to prevent.
    """
    out = []
    for j in range(idx, end + 1):
        _body, comment = _split_trailing_comment(lines[j].rstrip("\n"))
        if comment and comment.strip("# ").strip():
            out.append(comment.strip())
    return out


def _wrap_flow(values, lead: str, key: str, width: int = 90) -> list[str]:
    """Render a flow list, wrapped the way the config already wraps it.

    The live config wraps `enabled:` across three lines, aligned just after the
    `[`. Emitting one 200-character line would be valid YAML and a readability
    regression, so the original shape is reproduced.
    """
    items = [_emit_scalar(v) for v in values]
    one = "[" + ", ".join(items) + "]"
    if len(lead) + len(key) + 2 + len(one) <= width:
        return [f"{lead}{key}: {one}"]

    pad = " " * (len(lead) + len(key) + 3)       # aligns just after the "["
    lines: list[str] = []
    cur = "["
    for i, item in enumerate(items):
        piece = item + ("," if i < len(items) - 1 else "]")
        if cur == "[":
            cur += piece
        elif len(pad) + len(cur) + 1 + len(piece) <= width:
            cur += " " + piece
        else:
            lines.append(cur)
            cur = piece
    lines.append(cur)
    return [f"{lead}{key}: {lines[0]}"] + [pad + ln for ln in lines[1:]]


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
    flow_end = _flow_span(lines, idx)
    if flow_end is not None:
        return _parse_inline(_join_flow(lines, idx, flow_end))

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
    """Parse one value, ignoring any inline trailing comment.

    Without the comment strip, a documented line like
    `min_score: 6            # below this a posting is stored` reads back as the
    STRING '6    # below this...' rather than the number 6 — which is how the
    settings screen ended up rendering an empty box for every annotated value.
    """
    raw = _split_trailing_comment(raw)[0].strip()
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

    flow_end = _flow_span(lines, idx)
    if flow_end is not None or old_val.strip().startswith("["):   # flow list
        end = flow_end if flow_end is not None else idx
        # a wrapped flow list carries its comment on the closing line
        if flow_end is not None:
            _body, close_comment = _split_trailing_comment(
                lines[end].rstrip("\n"))
            comment = comment or close_comment
        if get_value(text, dotted) == list(values):               # true no-op
            return text
        rendered = _wrap_flow(list(values), lead, key)
        lifted = [f"{lead}# {c.lstrip('# ').strip()}{nl}"
                  for c in _interior_comments(lines, idx, end)]
        block = lifted + [f"{rendered[0]}{comment}{nl}"]
        block += [f"{r}{nl}" for r in rendered[1:]]
        return "".join(lines[:idx] + block + lines[end + 1:])

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

def ensure_section(text: str, name: str, body: list) -> str:
    """Append a documented top-level section that is not in the file yet.

    `set_scalar` refuses to invent structure, and rightly so: appending a key to
    the wrong parent silently corrupts a config. This is the deliberate
    exception -- create ONE top-level section, only when it is genuinely absent,
    so a setting introduced later can be stored in a config written before that
    setting existed. Every existing line is left exactly as it was.

    `body` is the section's inner lines, unindented; indentation is added here.
    """
    for line in text.splitlines():
        if _key_of(line) == name and _indent_of(line) == 0:
            return text                       # already there: nothing to do
    out = text
    if out and not out.endswith("\n"):
        out += "\n"
    if out.strip():
        out += "\n"
    out += f"{name}:\n"
    out += "".join(f"  {ln}\n" for ln in body)
    return out


def _read(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _replace_with_retry(tmp: str, path: Path, tries: int = 6) -> None:
    """os.replace, retrying a Windows sharing violation.

    Windows refuses to replace a file that another handle has open (no
    FILE_SHARE_DELETE by default), so a concurrent reader -- or a virus scanner
    that has just opened the new file -- makes the swap fail with WinError 5.
    The condition is transient by nature and does not exist on POSIX, which is
    why it only ever showed up on the Windows CI runner.
    """
    for attempt in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def write_atomic(path: Path, text: str) -> None:
    """Replace a file in one step, so no reader ever sees a partial write."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".jobscout-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, path)   # atomic on the same filesystem
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def edit(path: Path, op: str, dotted: str, value) -> bool:
    """Apply one edit to a file. Returns True when the file changed.

    A no-op (same value already present) leaves the file byte-identical.
    Serialised against other edits and written atomically -- see the note on
    _LOCK.
    """
    path = Path(path)
    fn = {"scalar": set_scalar, "list": set_list, "mapping": set_mapping,
          "section": ensure_section}.get(op)
    if fn is None:
        raise ConfigEditError(f"unknown op {op!r}")
    with _LOCK:
        before = _read(path)
        after = fn(before, dotted, value)
        if after != before:
            write_atomic(path, after)
            return True
        return False
