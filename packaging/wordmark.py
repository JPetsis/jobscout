"""Regenerate the wordmark lockup SVGs from the Inter release.

The lockup is a real asset, not a screenshot: the letters travel as outlines so
the SVG needs no font installed to render, and they cannot silently reflow when
somebody's system font differs. That also means the paths cannot be hand-edited,
which is why they are generated:

    curl -sL -o inter.zip https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip
    unzip -q inter.zip -d inter
    cd inter && npm init -y && npm i opentype.js
    cp <this repo>/packaging/wordmark_{outline,lockup}.js .
    uv run python <this repo>/packaging/wordmark.py .    # rewrites docs/

Inter is SIL OFL 1.1, so the outlines may be redistributed freely; the licence
does not require attribution in the file, but the source SVG says where the
shapes come from anyway, because a future reader will want to know.

The two settings that matter, both matching the app's own header: Inter
SemiBold, and letter-spacing of -0.01em (`styles.css`: `.brand h1`).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# The lockup belongs to the repo's own surfaces (README, releases), not to the
# running app -- the window draws its name as real text -- so it goes in docs/.
DOCS = Path(__file__).resolve().parent.parent / "docs"
STEPS = ("wordmark_outline.js", "wordmark_lockup.js")
WANTED = ("wordmark_outline.js", "wordmark_lockup.js")


def main(argv: list[str]) -> int:
    work = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    missing = [w for w in WANTED if not (work / w).exists()]
    if missing:
        print("not a prepared Inter build directory: missing " + ", ".join(missing))
        print((__doc__ or "").strip())
        return 2
    # The font sits at extras/ttf/ inside the Inter release, which may be the
    # working directory or a level or two above it if the scripts were dropped
    # into a subdirectory, so look rather than assume.
    font = None
    for base in (work, *list(work.parents)[:3]):
        found = sorted(base.rglob("Inter-SemiBold.ttf"))
        if found:
            font = found[0]
            break
    if font is None:
        print(f"no Inter-SemiBold.ttf in {work} or the three levels above it")
        print((__doc__ or "").strip())
        return 2
    env = {**os.environ, "FONT": str(font)}
    for step in STEPS:
        subprocess.run(["node", step], cwd=work, check=True, env=env)
    for name in ("logo-wordmark.svg", "logo-wordmark-dark.svg"):
        shutil.copy2(work / name, DOCS / name)
        print(f"  wrote {DOCS / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
