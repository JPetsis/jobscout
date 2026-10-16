"""Interactive triage: walk stored matches one by one and mark as you go.

The IO is injected (reader/opener/decide/write) so the flow is fully testable
and works both in a real terminal (single keypresses) and piped/scripted.
"""
from __future__ import annotations

import json

from . import salary as salmod
from . import ui
from .score import contract_label_from_reasons

HELP = "keys: o open · s saved · a applied · x dismiss · n next · q quit"


def _render(write, r, idx: int, total: int, min_score: float, strong_th: float) -> None:
    badge = ui.score_text(f"{r['score']:g}", r["score"], min_score, strong_th)
    try:
        reasons = json.loads(r["reasons"] or "[]")
    except (ValueError, TypeError):
        reasons = []
    pill = ui.contract_pill(contract_label_from_reasons(reasons))
    pill = f"  {pill}" if pill else ""
    write(f"\n[{idx}/{total}]  {badge}{pill}  {ui.bold(r['title'])}")
    meta = " · ".join(x for x in (r["company"], r["location"], salmod.fmt_row(r)) if x)
    if meta:
        write("        " + ui.dim(meta))
    if (r["verify_status"] or "") == "dead":
        write("        " + ui.red(f"⚠ looks dead ({r['verify_note'] or 'verify'})"))
    src = r["source"] + (f" · posted {r['posted_at'][:10]}" if r["posted_at"] else "")
    write("        " + ui.dim(src))
    if r["url"]:
        write("        " + ui.cyan(r["url"]))
    if reasons:
        write("        " + ui.dim("why: " + " · ".join(f"{lab} +{pts:g}" for lab, pts in reasons)))
    write("        " + ui.dim(HELP))


def run(rows, *, decide, opener, reader, write=print,
        min_score: float = 6.0, strong_th: float = 12.0) -> dict:
    """Review ``rows``; keep asking until the reader sends a mark or an exit key."""
    counts = {"saved": 0, "applied": 0, "dismissed": 0, "skipped": 0,
              "opened": 0, "quit": False, "total": len(rows)}
    if not rows:
        write(ui.dim("nothing to triage - no unmarked matches above the threshold"))
        return counts
    for idx, r in enumerate(rows, 1):
        _render(write, r, idx, len(rows), min_score, strong_th)
        while True:
            try:
                key = (reader() or "").strip().lower()
            except EOFError:
                counts["quit"] = True
                break
            if key in ("", "\r", "\n", "o"):          # Enter opens, like o
                if r["url"]:
                    opener(r["url"])
                    counts["opened"] += 1
                continue
            if key in ("s", "a", "x"):
                decide(r["id"], {"s": "saved", "a": "applied", "x": "dismissed"}[key])
                counts[{"s": "saved", "a": "applied", "x": "dismissed"}[key]] += 1
                break
            if key in ("n", " "):
                counts["skipped"] += 1
                break
            if key == "q":
                counts["quit"] = True
                break
            write(ui.dim("        " + HELP))
        if counts["quit"]:
            break
    return counts


def summary_text(counts: dict) -> str:
    bits = [f"{counts['saved']} saved", f"{counts['applied']} applied",
            f"{counts['dismissed']} dismissed", f"{counts['skipped']} skipped"]
    if counts["opened"]:
        bits.append(f"{counts['opened']} opened")
    tail = " (stopped early)" if counts["quit"] else ""
    return "triage done: " + " · ".join(bits) + tail
