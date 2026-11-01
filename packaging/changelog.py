#!/usr/bin/env python3
"""Build the release notes for a tag from the commits since the previous tag.

Run by the release job, and usable by hand:

    python3 packaging/changelog.py v0.2.2              # vs the tag before it
    python3 packaging/changelog.py v0.2.1 v0.2.2

Python rather than shell because this repo is Python and its CI matrix includes
Windows: a bash script there depends on MSYS quoting and on the checkout's line
endings, and both produced failures that said nothing about the notes.

PREFER HAND-WRITTEN NOTES. A commit subject is a development log, not release
notes: "one word costs one query" tells the person installing the app nothing.
`packaging/releases/<tag>.md` is published verbatim when it exists, and the
generated list below is only the fallback for a tag that has no such file. Write
that file before tagging; it is part of the release, not an afterthought.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Order matters: it is the order the sections appear in.
GROUPS = [
    ("Features", ("feat",)),
    ("Fixes", ("fix",)),
    ("Performance", ("perf",)),
    ("Documentation", ("docs",)),
    ("Other", ("chore", "ci", "test", "refactor", "build", "style")),
]

# Says nothing to someone reading the notes.
IGNORED = ("release",)

#: A commit whose whole message is a version number. This repo bumps with
#: `chore(release): 0.4.0`, whose TYPE is `chore`, so the kind test above misses
#: it and "0.4.0" lands in the notes as if it were a change.
_VERSION_ONLY = re.compile(r"^v?\d+\.\d+\.\d+$")


def is_release_commit(subject: str) -> bool:
    """A version bump, by kind (`release: x`) or by scope (`chore(release): x`)."""
    head, sep, rest = subject.partition(":")
    if not sep:
        return False
    kind = head.split("(", 1)[0].strip().lower()
    scope = head.partition("(")[2].rstrip(")").strip().lower()
    if kind in IGNORED or scope in IGNORED:
        return True
    return bool(_VERSION_ONLY.match(rest.strip()))


def git(*args: str, cwd: str | None = None) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout


def previous_tag(tag: str, cwd: str | None = None) -> str:
    """The nearest v* tag reachable from the commit before `tag`, or ''."""
    r = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", "v*", f"{tag}^"],
        capture_output=True, text=True, cwd=cwd)
    return r.stdout.strip() if r.returncode == 0 else ""


def split_subject(subject: str) -> tuple[str, str] | None:
    """('feat', 'a feature') for 'feat(app): a feature'; None when unlabelled."""
    head, sep, rest = subject.partition(":")
    if not sep or not rest.strip():
        return None
    kind = head.split("(", 1)[0].strip().lower()
    if not kind.isalpha():
        return None
    return kind, rest.strip()


RELEASES = Path(__file__).resolve().parent / "releases"


def hand_written(target: str) -> str | None:
    """The curated notes for this tag, or None when nobody wrote them."""
    try:
        text = (RELEASES / f"{target}.md").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def build_notes(prev: str, target: str, cwd: str | None = None) -> str:
    if prev:
        out = [f"## What changed since `{prev}`", ""]
        rng = f"{prev}..{target}"
    else:
        out = ["## What changed", ""]
        rng = target

    raw = git("log", "--no-merges", "--pretty=format:%s", "--reverse", rng, cwd=cwd)
    subjects = [s.strip() for s in raw.splitlines() if s.strip()]
    if not subjects:
        return out[0] + "\n\n_No commits between the two tags._\n"

    buckets: dict[str, list[str]] = {title: [] for title, _ in GROUPS}
    unlabelled: list[str] = []
    for subject in subjects:
        if is_release_commit(subject):
            continue
        parsed = split_subject(subject)
        if parsed is None:
            unlabelled.append(subject)
            continue
        kind, text = parsed
        if kind in IGNORED:
            continue
        for title, kinds in GROUPS:
            if kind in kinds:
                buckets[title].append(text)
                break
        else:
            # An unfamiliar prefix keeps its whole subject rather than being
            # dropped: a release note that silently omits a change is worse than
            # a slightly untidy one.
            unlabelled.append(subject)
    buckets["Other"].extend(unlabelled)

    for title, _ in GROUPS:
        if not buckets[title]:
            continue
        out.append(f"### {title}")
        out.append("")
        out.extend(f"- {text}" for text in buckets[title])
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) == 2:
        prev, target = args
    elif len(args) == 1:
        target = args[0]
        prev = previous_tag(target)
    else:
        print("usage: changelog.py <tag>            # vs the tag before it",
              file=sys.stderr)
        print("       changelog.py <from> <to>", file=sys.stderr)
        return 2
    curated = hand_written(target)
    if curated:
        sys.stdout.write(curated + "\n")
        return 0
    # No curated file: publish the commit subjects, and say so on stderr so the
    # release log shows which release went out without notes.
    print(f"warning: no packaging/releases/{target}.md; using commit subjects",
          file=sys.stderr)
    sys.stdout.write(build_notes(prev, target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
