"""Detect the ATS behind a careers page and add its board token to the config.

Detection is pattern-based over the given URLs and the fetched page (scripts
often embed the board URL). Validation hits the ATS's public jobs API so a
token only ever gets added when it demonstrably returns jobs.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .http import FetchError, Fetcher

PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)", re.I)),
    # Personio was dropped 2026-10-03: its customer boards now 307 to
    # personio.com for every host (fabricated ones included) and the career
    # pages moved to <company>.app.personio.com, which is 401 + Disallow: /.
    # Re-add only if a public endpoint ever serves positions again.
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    # Teamtailor boards live at <token>.teamtailor.com; the token is the
    # subdomain. Infrastructure subdomains must not be mistaken for boards.
    ("teamtailor", re.compile(r"([A-Za-z0-9-]+)\.teamtailor\.com", re.I)),
]

_TT_INFRA = {"www", "api", "cdn", "app", "support", "help", "blog", "status"}

VALIDATE = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{t}",
    "recruitee": "https://{t}.recruitee.com/api/offers/",
    "lever": "https://api.lever.co/v0/postings/{t}?mode=json",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{t}?details=true",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{t}/postings",
    # Teamtailor boards expose a public JSON Feed; "items" is the role list.
    "teamtailor": "https://{t}.teamtailor.com/jobs.json",
}
_HINT = {"greenhouse": "https://boards.greenhouse.io/<token>",
         "ashby": "https://jobs.ashbyhq.com/<token>",
         "recruitee": "https://<company>.recruitee.com",
         "lever": "https://jobs.lever.co/<token>",
         "workable": "https://apply.workable.com/<token>/",
         "smartrecruiters": "https://jobs.smartrecruiters.com/<id>",
         "teamtailor": "https://<token>.teamtailor.com/jobs"}


def detect(text: str = "", *urls: str) -> list[tuple[str, str]]:
    """(source, token) candidates: the given URLs first, then the page text."""
    out, seen = [], set()

    def add(src: str, tok: str) -> None:
        if src == "teamtailor" and tok.lower() in _TT_INFRA:
            return
        if (src, tok) not in seen:
            seen.add((src, tok))
            out.append((src, tok))

    for u in urls:
        for src, rx in PATTERNS:
            for m in rx.finditer(u or ""):
                add(src, m.group(1))
    for src, rx in PATTERNS:
        for m in rx.finditer(text or ""):
            add(src, m.group(1))
    return out


def validate(fetcher: Fetcher, source: str, token: str) -> tuple[bool, str]:
    """Probe the public jobs API. Returns (ok, detail) with a job count when known."""
    tmpl = VALIDATE.get(source)
    if not tmpl:
        return False, "no validator for this ATS"
    try:
        status, text = fetcher.get(tmpl.format(t=token))
    except FetchError as e:
        return False, f"fetch error ({e.message[:50]})"
    if status != 200:
        return False, f"HTTP {status}"
    try:
        data = json.loads(text)
    except ValueError:
        return False, "API answered, but not JSON"
    if isinstance(data, dict):
        for key in ("jobs", "content", "results", "items"):
            if isinstance(data.get(key), list):
                return True, f"{len(data[key])} open role(s)"
        return True, "board answered"
    if isinstance(data, list):
        return True, f"{len(data)} open role(s)"
    return True, "board answered"


def _inject(text: str, source: str, token: str) -> tuple[str, str]:
    """Add ``token`` to ``companies.<source>.tokens`` in raw YAML text.

    Returns (new_text, status) with status in {added, exists, section-created}.
    Only the touched line changes; comments and formatting stay intact.
    """
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if re.match(r"^companies\s*:", ln)), None)
    if start is None:
        sep = "" if (not text or text.endswith("\n")) else "\n"
        block = (f"companies:\n  {source}:\n    # board tokens from {_HINT.get(source, '')}\n"
                 f"    tokens: [{token}]\n")
        return text + sep + block, "section-created"

    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() and not lines[i][0].isspace()), len(lines))
    sub = next((i for i in range(start + 1, end)
                if re.match(rf"^\s{{2}}{re.escape(source)}\s*:", lines[i])), None)
    if sub is None:
        block = (f"  {source}:\n    # board tokens from {_HINT.get(source, '')}\n"
                 f"    tokens: [{token}]\n")
        return "".join(lines[:end]) + block + "".join(lines[end:]), "section-created"

    sub_end = next((i for i in range(sub + 1, end)
                    if lines[i].strip() and re.match(r"^\s{2}\S", lines[i])), end)
    for i in range(sub + 1, sub_end):
        m = re.match(r"^(\s*)tokens\s*:\s*(.*?)\s*$", lines[i])
        if not m:
            continue
        indent, rest = m.group(1), m.group(2)
        if rest.startswith("["):                      # inline flow list
            items = [t.strip() for t in rest.strip("[]").split(",") if t.strip()]
            if token in items:
                return text, "exists"
            items.append(token)
            lines[i] = f"{indent}tokens: [{', '.join(items)}]\n"
            return "".join(lines), "added"
        tokens_here = [rest] if rest else []          # block list
        last_item = i
        item_indent = None
        for j in range(i + 1, sub_end):
            item_m = re.match(r"^(\s*)-\s*(\S.*?)\s*$", lines[j])
            if item_m:
                if item_indent is None:
                    # Reuse the EXISTING items' indent: yaml.safe_dump puts
                    # items at the key's own indent, hand-written configs
                    # indent them by 2 more. A deeper insert folds the new
                    # item into the previous scalar (silent corruption:
                    # ["teamviewer", "sandboxinteractive"] became
                    # ["teamviewer - sandboxinteractive"]).
                    item_indent = item_m.group(1)
                tokens_here.append(item_m.group(2))
                last_item = j
            elif lines[j].strip():
                break
        if token in tokens_here:
            return text, "exists"
        lines.insert(last_item + 1, f"{item_indent or (indent + '  ')}- {token}\n")
        return "".join(lines), "added"

    lines.insert(sub + 1, f"    tokens: [{token}]\n")
    return "".join(lines), "added"


def _write(path: Path, text: str) -> None:
    """Write the config atomically.

    A crash mid-write must not leave a half-written config: this file holds the
    137 comment lines that document it, and every edit here is line-wise for the
    same reason -- losing the tail of the file would lose the documentation with
    it. Same discipline as `cfgwrite`.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def add_token(path: Path, source: str, token: str) -> str:
    """Edit config.yaml on disk. Returns the _inject status."""
    text = path.read_text(encoding="utf-8")
    new, status = _inject(text, source, token)
    if status != "exists":
        _write(path, new)
    return status


def remove_token(path: Path, source: str, token: str) -> str:
    """Stop following one employer. Returns "removed" or "missing".

    Goes through `cfgwrite` rather than a local regex: this is the same edit the
    settings screen makes, and the comments and the multi-line `tokens: [...]`
    span have to survive it like any other config write.
    """
    from . import cfgwrite
    text = path.read_text(encoding="utf-8")
    values = cfgwrite.get_value(text, f"companies.{source}.tokens")
    if not isinstance(values, list):
        return "missing"
    left = [v for v in values if str(v) != str(token)]
    if len(left) == len(values):
        return "missing"
    _write(path, cfgwrite.set_list(text, f"companies.{source}.tokens", left))
    return "removed"


def _enable_in_list(text: str, source: str) -> tuple[str, str]:
    """Add ``source`` to ``sources.enabled``. (new_text, status).

    Handles both styles: the template's inline flow list and a block list
    (what yaml.safe_dump produces, i.e. any config that has been through a
    YAML round-trip tool). Block items keep their own indent for the same
    reason _inject does.
    """
    m = re.search(r"(?m)^(\s*)enabled\s*:\s*\[(.*?)\]", text, re.S)
    if m:
        inner = m.group(2)
        if source in [t.strip() for t in inner.split(",") if t.strip()]:
            return text, "exists"
        new_inner = (inner.rstrip() + f", {source}") if inner.strip() else f" {source}"
        return text[:m.start(2)] + new_inner + text[m.end(2):], "added"

    m = re.search(r"(?m)^(\s*)enabled\s*:\s*(?:#.*)?\n", text)
    if not m:
        return text, "missing"
    key_indent = m.group(1)
    item_rx = re.compile(r"^(\s*)-\s*(\S.*?)\s*$")
    pos = last_end = m.end()
    item_indent = None
    found: list[str] = []
    while pos < len(text):
        nl = text.find("\n", pos)
        line = text[pos:nl if nl != -1 else len(text)]
        im = item_rx.match(line)
        if im and (item_indent is None or im.group(1) == item_indent):
            item_indent = item_indent or im.group(1)
            found.append(im.group(2))
            last_end = (nl + 1) if nl != -1 else len(text)
            pos = last_end
        else:
            break
    if source in found:
        return text, "exists"
    indent = item_indent or (key_indent + "  ")
    return text[:last_end] + f"{indent}- {source}\n" + text[last_end:], "added"


def enable_source(path: Path, source: str) -> str:
    """Make sure a configured board is actually scanned. Returns the status."""
    text = path.read_text(encoding="utf-8")
    new, status = _enable_in_list(text, source)
    if status == "added":
        path.write_text(new, encoding="utf-8")
    return status
