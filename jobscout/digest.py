"""Markdown digest writer."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import db as dbm
from . import salary as salmod
from .config import Config
from .textutil import snippet


def _line(r, why: bool = True) -> str:
    title = f"**[{r['title']}]({r['url']})**" if r["url"] else f"**{r['title']}**"
    company = f" — {r['company']}" if r["company"] else ""
    loc = f" · {r['location']}" if r["location"] else ""
    edge = []
    sal = salmod.fmt_row(r)
    if sal:
        edge.append(f"**{sal}**")
    edge.append(r["source"])
    if r["posted_at"]:
        edge.append(f"posted {r['posted_at'][:10]}")
    if r["status"] and r["status"] != "new":
        edge.append(r["status"])
    if (r["verify_status"] or "") == "dead":
        edge.append("⚠ looks dead")
    if why:
        try:
            reasons = json.loads(r["reasons"] or "[]")
        except ValueError:
            reasons = []
        if reasons:
            edge.append("why: " + " · ".join(f"{lab} +{pts:g}" for lab, pts in reasons))
    return f"- `{r['score']:g}` {title}{company}{loc}  \n  ↳ {' · '.join(edge)}"


def write_digest(cfg: Config, conn, run_id: int | None = None, top: int | None = None):
    run = dbm.get_run(conn, run_id)
    if run is None:
        raise SystemExit("no runs yet — run: jobscout scan")
    run_id = run["id"]
    stats = json.loads(run["stats"] or "{}")

    min_score = float(cfg.prefs.get("min_score", 6))
    strong_th = float(cfg.prefs.get("strong_threshold", 12))
    total_matches, total_strong = dbm.count_matches(conn, min_score, strong_th)

    rows = dbm.list_postings(conn, run_id=run_id, exclude_filtered=False, include_dups=True, limit=10000)
    new = [r for r in rows if r["dup_of"] is None]
    matches = [r for r in new if not r["filtered"] and r["score"] >= min_score]
    strongs = [r for r in matches if r["score"] >= strong_th]
    others = [r for r in matches if r["score"] < strong_th]
    below = [r for r in new if not r["filtered"] and r["score"] < min_score]
    filtered = [r for r in new if r["filtered"]]
    dups = [r for r in rows if r["dup_of"] is not None]
    if top:
        strongs, others = strongs[:top], others[:top]

    date = (run["started_at"] or "")[:10]
    errors = stats.get("errors") or []
    md = [
        f"# jobscout digest — {date}",
        "",
        f"**Run #{run_id}** · {stats.get('requests', 0)} requests · {len(errors)} error(s)",
        "",
        "| this run | count |",
        "| --- | ---: |",
        f"| new postings | {len(new)} |",
        f"| matches | {len(matches)} · {len(strongs)} strong |",
        f"| filtered out | {len(filtered)} |",
        f"| duplicates | {len(dups)} |",
        "",
        f"_Only postings seen for the first time in this run are listed. Your current profile "
        f"matches **{total_matches}** stored postings ({total_strong} strong) — "
        f"`jobscout digest --all` lists them._",
        "",
    ]
    if strongs:
        md += [f"## 🔥 Strong matches (score ≥ {strong_th:g})", ""]
        md += [_line(r) for r in strongs]
        md.append("")
    if others:
        md += [f"## ✅ Moderate matches (score ≥ {min_score:g})", ""]
        md += [_line(r) for r in others]
        md.append("")
    if not matches:
        md += ["No matches above threshold in this run.", ""]
    if below:
        md += [f"## 👀 New below threshold ({min(len(below), 10)} of {len(below)})", ""]
        for r in below[:10]:
            md.append(f"- `{r['score']:g}` {r['title']} — {r['company'] or '?'} — {r['location'] or '?'}")
        md.append("")
    if filtered:
        md += [f"## 🚫 Filtered out ({min(len(filtered), 8)} of {len(filtered)})", ""]
        for r in filtered[:8]:
            md.append(f"- {r['title']} — {r['company'] or '?'} — `{r['filtered_reason']}`")
        md.append("")
    if dups:
        md += [f"## 🔁 Duplicates ({len(dups)})", ""]
        for r in dups[:5]:
            md.append(f"- {r['title']} — {r['company'] or '?'} — {r['source']} (duplicate of #{r['dup_of']})")
        md.append("")
    md += ["---", ""]
    sources = stats.get("sources") or {}
    if sources:
        md += ["| source | fetched | new |", "| --- | ---: | ---: |"]
        for src, s in sources.items():
            md.append(f"| {src} | {s.get('fetched', 0)} | {s.get('new', 0)} |")
        md.append("")
    if errors:
        md += [f"**{len(errors)} error(s)**", ""] + [f"- `{e}`" for e in errors[:10]] + [""]
    dead_n = conn.execute(
        "SELECT COUNT(*) n FROM postings WHERE verify_status='dead' "
        "AND status IN ('saved','applied')").fetchone()["n"]
    if dead_n:
        md += [f"⚠ **{dead_n} saved/applied listing(s) look dead** — "
               f"run `jobscout verify` to re-check and list them.", ""]

    path = cfg.root / "reports" / f"{date}-digest.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(md), encoding="utf-8")

    summary = {
        "run_id": run_id, "new": len(new), "matches": len(matches),
        "strong": len(strongs), "filtered": len(filtered), "dups": len(dups),
        "top": [(r["id"], r["title"], r["company"], r["score"]) for r in (strongs + others)[:5]],
    }
    return path, summary


def write_matches_digest(cfg: Config, conn, top: int | None = None):
    """Everything stored that matches the current profile, across all runs.

    This is the view to use after editing preferences (run `jobscout rescore`
    first if the scores are stale). Dismissed postings are excluded.
    """
    min_score = float(cfg.prefs.get("min_score", 6))
    strong_th = float(cfg.prefs.get("strong_threshold", 12))
    rows = [r for r in dbm.list_postings(conn, min_score=min_score, limit=100000)
            if r["status"] != "dismissed"]
    strongs = [r for r in rows if r["score"] >= strong_th]
    others = [r for r in rows if r["score"] < strong_th]
    shown_s = strongs[:top] if top else strongs
    shown_o = others[:top] if top else others

    date = datetime.now().strftime("%Y-%m-%d")
    md = [
        f"# jobscout matches — {date}",
        "",
        f"**{len(rows)}** stored postings match your current profile (score ≥ {min_score:g}): "
        f"**{len(strongs)} strong** · {len(others)} moderate. Dismissed postings excluded.",
        "",
    ]
    if not rows:
        md += ["No matches yet. Run `jobscout scan` first.", ""]
    if strongs:
        head = f"## 🔥 Strong matches (score ≥ {strong_th:g})"
        if top and len(shown_s) < len(strongs):
            head = f"## 🔥 Strong matches ({len(shown_s)} of {len(strongs)})"
        md += [head, ""] + [_line(r) for r in shown_s] + [""]
    if others:
        head = f"## ✅ Moderate matches (score ≥ {min_score:g})"
        if top and len(shown_o) < len(others):
            head = f"## ✅ Moderate matches ({len(shown_o)} of {len(others)})"
        md += [head, ""] + [_line(r) for r in shown_o] + [""]
    md += ["---", "",
           "_After editing preferences, run `jobscout rescore` to refresh these scores._", ""]
    dead_n = conn.execute(
        "SELECT COUNT(*) n FROM postings WHERE verify_status='dead' "
        "AND status IN ('saved','applied')").fetchone()["n"]
    if dead_n:
        md += [f"⚠ **{dead_n} saved/applied listing(s) look dead** — "
               f"run `jobscout verify` to re-check and list them.", ""]

    path = cfg.root / "reports" / f"{date}-matches.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(md), encoding="utf-8")
    summary = {
        "matches": len(rows), "strong": len(strongs),
        "top": [(r["id"], r["title"], r["company"], r["score"]) for r in rows[:5]],
    }
    return path, summary
