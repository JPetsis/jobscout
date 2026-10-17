#!/usr/bin/env python3
"""Build the release notes for a tag from the commits since the previous tag.

Run by the release job, and usable by hand:

    python3 packaging/changelog.py v0.2.2              # vs the tag before it
    python3 packaging/changelog.py v0.2.1 v0.2.2

Python rather than shell because this repo is Python and its CI matrix includes
Windows: a bash script there depends on MSYS quoting and on the checkout's line
endings, and both produced failures that said nothing about the notes.

The repo commits straight to main with conventional prefixes and no pull
requests, so GitHub's own `generate_release_notes` has nothing to work from --
the commits ARE the changelog.
"""
from __future__ import annotations

import subprocess
import sys

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
    sys.stdout.write(build_notes(prev, target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
