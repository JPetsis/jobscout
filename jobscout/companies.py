"""Detect the ATS behind a careers page and add its board token to the config.

Detection is pattern-based over the given URLs and the fetched page (scripts
often embed the board URL). Validation hits the ATS's public jobs API so a
token only ever gets added when it demonstrably returns jobs.
"""
from __future__ import annotations

import json
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
]

VALIDATE = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{t}",
    "recruitee": "https://{t}.recruitee.com/api/offers/",
    "lever": "https://api.lever.co/v0/postings/{t}?mode=json",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{t}?details=true",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{t}/postings",
}
_HINT = {"greenhouse": "https://boards.greenhouse.io/<token>",
         "ashby": "https://jobs.ashbyhq.com/<token>",
         "recruitee": "https://<company>.recruitee.com",
         "lever": "https://jobs.lever.co/<token>",
         "workable": "https://apply.workable.com/<token>/",
         "smartrecruiters": "https://jobs.smartrecruiters.com/<id>"}


def detect(text: str = "", *urls: str) -> list[tuple[str, str]]:
    """(source, token) candidates: the given URLs first, then the page text."""
    out, seen = [], set()

    def add(src: str, tok: str) -> None:
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
        for key in ("jobs", "content", "results"):
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
        for j in range(i + 1, sub_end):
            item_m = re.match(r"^\s*-\s*(\S.*?)\s*$", lines[j])
            if item_m:
                tokens_here.append(item_m.group(1))
                last_item = j
            elif lines[j].strip():
                break
        if token in tokens_here:
            return text, "exists"
        lines.insert(last_item + 1, f"{indent}  - {token}\n")
        return "".join(lines), "added"

    lines.insert(sub + 1, f"    tokens: [{token}]\n")
    return "".join(lines), "added"


def add_token(path: Path, source: str, token: str) -> str:
    """Edit config.yaml on disk. Returns the _inject status."""
    text = path.read_text(encoding="utf-8")
    new, status = _inject(text, source, token)
    if status != "exists":
        path.write_text(new, encoding="utf-8")
    return status


def _enable_in_list(text: str, source: str) -> tuple[str, str]:
    """Add ``source`` to ``sources.enabled`` (inline list). (new_text, status)."""
    m = re.search(r"(?m)^(\s*)enabled\s*:\s*\[(.*?)\]", text, re.S)
    if not m:
        return text, "missing"
    inner = m.group(2)
    if source in [t.strip() for t in inner.split(",") if t.strip()]:
        return text, "exists"
    new_inner = (inner.rstrip() + f", {source}") if inner.strip() else f" {source}"
    return text[:m.start(2)] + new_inner + text[m.end(2):], "added"


def enable_source(path: Path, source: str) -> str:
    """Make sure a configured board is actually scanned. Returns the status."""
    text = path.read_text(encoding="utf-8")
    new, status = _enable_in_list(text, source)
    if status == "added":
        path.write_text(new, encoding="utf-8")
    return status
