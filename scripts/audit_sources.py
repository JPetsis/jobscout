"""Ask every connector the same questions, and print the answers.

The app grew one source at a time, and each was checked by opening a card and
looking at it. That finds what a person happens to click, not what is broken:
Jobware shipped adverts with no body for months because the one card anyone
opened happened to work.

This audit runs each enabled source with one query, then measures what came back:
how many postings, how many carry a description, whether that description is
prose rather than the page's own scripts, whether the required fields are there,
and whether a sample of the stored links still answers. It prints a table and
writes the same data as JSON so a later run can be compared against it.

Usage:
    uv run python scripts/audit_sources.py                 # enabled sources
    uv run python scripts/audit_sources.py --all           # every registered
    uv run python scripts/audit_sources.py --only stepstone,xing --json out.json
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobscout import sources                                   # noqa: E402
from jobscout import db as dbm                                  # noqa: E402
from jobscout.config import load_config, source_settings        # noqa: E402
from jobscout.http import Fetcher                               # noqa: E402

_HTML = re.compile(r"<(?:div|span|p|br|li|script|style)\b", re.I)
_CODE = re.compile(r"(function\s*\(|=>|\{\s*\"|var\(--|clamp\()")


def _prose(text: str) -> bool:
    """Does this look like an advert rather than markup or a stylesheet?"""
    if not text or len(text) < 120:
        return False
    if _HTML.search(text[:4000]) or _CODE.search(text[:2000]):
        return False
    letters = sum(ch.isalpha() for ch in text)
    return letters / max(1, len(text)) > 0.5


def stored_rows(conn, name: str, limit: int = 100) -> list[dict]:
    """What the store already holds for one source, as the window would show it."""
    try:
        rows = conn.execute(
            "SELECT title, company, location, url, posted_at, description "
            "FROM postings WHERE source=? AND dup_of IS NULL LIMIT ?",
            (name, limit)).fetchall()
    except Exception:                                        # noqa: BLE001
        return []
    return [dict(r) for r in rows]


def audit_stored(rows: list[dict]) -> dict:
    if not rows:
        return {"stored": 0, "stored_text": 0, "stored_prose": 0, "stored_chars": 0}
    with_desc = [r for r in rows if (r.get("description") or "").strip()]
    prose = [r for r in with_desc if _prose(r["description"])]
    lengths = [len(r["description"]) for r in with_desc]
    return {
        "stored": len(rows),
        "stored_text": len(with_desc),
        "stored_prose": len(prose),
        "stored_chars": int(statistics.median(lengths)) if lengths else 0,
    }


def audit_one(name: str, cfg, fetcher_delay: float = 0.3, conn=None) -> dict:
    settings = dict(source_settings(cfg, name))
    queries = settings.get("queries") or []
    if queries:
        settings["queries"] = queries[:1]        # one query is enough to judge
        settings["max_pages"] = 1
    fetcher = Fetcher(host_delay=fetcher_delay)
    started = time.perf_counter()
    error = ""
    postings = []
    try:
        postings = list(sources.REGISTRY[name].fetch(settings, fetcher) or [])
    except Exception as exc:                                  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"[:160]
    took = time.perf_counter() - started

    with_desc = [p for p in postings if (p.description or "").strip()]
    prose = [p for p in with_desc if _prose(p.description)]
    lengths = [len(p.description or "") for p in with_desc]

    def share(pred) -> float:
        return round(100 * sum(1 for p in postings if pred(p)) / len(postings), 0) if postings else 0.0

    # A source may store no text and still be able to produce it on demand:
    # the body is fetched when a card is opened, not at scan time. Measuring only
    # the store would call that a broken connector, so ask the detail path too.
    detail_prose = detail_tried = 0
    mod = sources.REGISTRY[name]
    for p in postings[:2]:
        if (p.description or "").strip() or not p.url:
            continue
        detail_tried += 1
        try:
            if hasattr(mod, "fetch_detail"):
                text = mod.fetch_detail(p, fetcher) or ""
            else:
                from jobscout import detail as detailmod
                text = detailmod.html_detail(p.url, fetcher) or ""
        except Exception:                                     # noqa: BLE001
            text = ""
        detail_prose += 1 if _prose(text) else 0

    links_ok = links_checked = 0
    for p in postings[:2]:
        if not p.url:
            continue
        links_checked += 1
        try:
            status, _ = fetcher.get(p.url)
            links_ok += 1 if status == 200 else 0
        except Exception:                                     # noqa: BLE001
            pass

    stored = audit_stored(stored_rows(conn, name) if conn is not None else [])

    return {
        "source": name,
        **stored,
        "label": getattr(sources.REGISTRY[name], "LABEL", name),
        "count": len(postings),
        "with_description": len(with_desc),
        "prose": len(prose),
        "median_chars": int(statistics.median(lengths)) if lengths else 0,
        "pct_url": share(lambda p: bool(p.url)),
        "pct_company": share(lambda p: bool((p.company or "").strip())),
        "pct_location": share(lambda p: bool((p.location or "").strip())),
        "pct_posted": share(lambda p: bool(p.posted_at)),
        "links_ok": f"{links_ok}/{links_checked}" if links_checked else "-",
        "detail_prose": detail_prose,
        "detail_tried": detail_tried,
        "seconds": round(took, 1),
        "errors": list(fetcher.errors)[:2] + ([error] if error else []),
        "sample": [p.title[:60] for p in postings[:2]],
    }


def verdict(row: dict) -> str:
    if row["count"] == 0:
        return "EMPTY" if not row["errors"] else "FAILED"
    if row["stored"] and row["stored_text"] == 0:
        # The body exists but is fetched when a card is opened, not at scan time.
        # That is a different problem from a connector that cannot read it at all,
        # and it matters because scoring happens before anyone opens a card.
        return "LAZY" if row.get("detail_prose") else "NO TEXT"
    if row["stored"] and row["stored_prose"] < row["stored_text"]:
        return "BAD TEXT"
    if row["count"] == 0:
        return "EMPTY" if not row["errors"] else "FAILED"
    if row["prose"] < row["with_description"]:
        return "BAD TEXT"
    if row["pct_company"] < 90 or row["pct_url"] < 100:
        return "FIELDS"
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every registered source")
    ap.add_argument("--only", default="", help="comma separated names")
    ap.add_argument("--json", default="", help="write the rows here")
    ap.add_argument("--delay", type=float, default=0.3)
    args = ap.parse_args()

    cfg = load_config()
    conn = dbm.connect(cfg.root / "jobscout.db")
    if args.only:
        names = [n.strip() for n in args.only.split(",") if n.strip()]
    elif args.all:
        names = list(sources.SOURCE_NAMES)
    else:
        names = [n for n in sources.SOURCE_NAMES if n in cfg.enabled]

    rows = []
    print(f"{'source':22s} {'live':>4s} {'stored':>6s} {'text':>5s} {'prose':>5s} "
          f"{'chars':>6s} {'detail':>6s} {'links':>6s}  verdict")
    for name in names:
        row = audit_one(name, cfg, args.delay, conn)
        row["verdict"] = verdict(row)
        rows.append(row)
        print(f"{row['source'][:22]:22s} {row['count']:4d} {row['stored']:6d} "
              f"{row['stored_text']:5d} {row['stored_prose']:5d} {row['stored_chars']:6d} "
              f"{row['detail_prose']}/{row['detail_tried']:<2d} {row['links_ok']:>6s}  {row['verdict']}")
        if row["errors"]:
            for e in row["errors"]:
                print(f"{'':24s} ! {e}")
        sys.stdout.flush()

    if args.json:
        Path(args.json).write_text(json.dumps({
            "when": datetime.now().isoformat(timespec="seconds"),
            "rows": rows,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    bad = [r for r in rows if r["verdict"] != "ok"]
    print(f"\n{len(rows)} sources, {len(bad)} need attention: "
          + ", ".join(f"{r['source']}({r['verdict']})" for r in bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
