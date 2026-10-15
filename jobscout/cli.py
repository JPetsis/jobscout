"""jobscout command line interface."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

from . import __version__
from . import db as dbm
from . import detail as detailmod
from . import digest as digestmod
from . import companies as companiesmod
from . import discover
from . import goals as goalsmod
from . import liveness
from . import salary as salmod
from . import triage as triagemod
from . import update as updatemod
from . import ui
from . import backup as backupmod
from . import scanner
from .config import home, init_workspace, load_config
from .http import FetchError, Fetcher
from .models import Posting

from .sources import REGISTRY

STATUSES = ("new", "saved", "applied", "dismissed")


def _local(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return ts or ""


# ---------------------------------------------------------------- commands

def cmd_init(args) -> int:
    root = home()
    created = init_workspace(root)
    print(f"workspace: {root}")
    if created:
        for c in created:
            print(f"  created {c}")
    else:
        print("  (already initialised)")
    print("\nnext steps:")
    print("  1. jobscout config --edit    (your roles, locations, sources)")
    print("  2. jobscout scan")
    print("  3. jobscout digest")
    return 0


def cmd_restore(args) -> int:
    """Put a backup back. Thin view over backup.restore()."""
    cfg = load_config()
    dest = Path(args.to).expanduser() if args.to else backupmod.default_destination()

    if args.from_archive:
        archive = Path(args.from_archive).expanduser()
    else:
        rows = backupmod.list_backups(dest)
        if not rows:
            print(ui.red(f"no backups found in {dest}"))
            print(ui.dim("  `jobscout backup` writes one; `jobscout backup --list` shows them"))
            return 1
        archive = rows[0]["path"]                    # newest first

    info = backupmod.describe(archive)
    print(ui.bold("restore") + ui.dim(f"  {archive.name}"))
    if info.get("created"):
        print(ui.dim(f"  taken {info['created']}"))
    if info.get("postings") is not None:
        print(f"  {ui.cyan(str(info['postings']))} postings · {info.get('marked') or 0} marked")

    if not backupmod.workspace_is_empty(cfg.root) and not args.overwrite:
        print()
        print(ui.red("your workspace already has a config or database") +
              ui.dim(f"  ({cfg.root})"))
        print(ui.dim("  restoring would replace it. re-run with --overwrite;"))
        print(ui.dim("  your current state is copied to a new backup first."))
        return 1

    if not args.yes and not backupmod.workspace_is_empty(cfg.root):
        print()
        print(ui.dim("  this replaces your current workspace. re-run with --yes."))
        return 1

    try:
        res = backupmod.restore(archive, cfg.root, overwrite=args.overwrite)
    except (OSError, ValueError, tarfile.TarError) as e:
        print(ui.red(f"restore failed: {type(e).__name__}: {e}"))
        return 1

    c = res["counts"]
    print()
    print(ui.bold("restored") + ui.dim(f"  {cfg.root}"))
    print(f"{ui.cyan(str(c['postings']))} postings · {c['marked']} marked · "
          f"last run #{c['last_run']}")
    if res.get("safety_backup"):
        print(ui.dim(f"your previous state was saved to {res['safety_backup']}"))
    return 0


def cmd_update(args) -> int:
    """Check for a newer build, and stage or install it. Thin view over update.py."""
    cfg = load_config()

    if args.from_archive:
        # Installing a build the user already has: no network, no version check.
        try:
            res = updatemod.update(cfg.root, from_archive=Path(args.from_archive),
                                   apply=not args.stage_only,
                                   relaunch=not args.no_relaunch,
                                   progress=lambda m: print(ui.dim("  " + m)))
        except updatemod.UpdateError as e:
            print(ui.red(f"update failed: {e}"))
            return 1
        print(ui.bold("staged from a local build"))
        print(ui.dim(f"  {res['staged']}"))
        if res.get("updated"):
            print(ui.dim("  jobscout will restart itself once this process exits."))
        else:
            print(ui.dim(f"  staged only. apply with: jobscout update --from {args.from_archive}"))
        return 0

    try:
        info = updatemod.check(cfg.root, force=True, include_prerelease=args.prerelease)
    except Exception as e:                                     # noqa: BLE001
        print(ui.red(f"could not check for updates: {e}"))
        return 1

    if not info.get("ok"):
        print(ui.red(f"could not check for updates: {info.get('error')}"))
        return 1

    cur = info["current"]
    if not info.get("update_available"):
        if info.get("latest") is None:
            # Distinct from "you are current": nothing has been PUBLISHED yet
            # (a draft is not downloadable by anyone but the owner), so there
            # is nothing to compare against rather than nothing newer.
            print(ui.bold("jobscout") + f" {cur}")
            print(ui.dim("  no releases published yet — nothing to compare against"))
            print(ui.dim(f"  {updatemod.HOMEPAGE}"))
        else:
            print(ui.bold("jobscout") + f" {cur}" +
                  ui.dim(f"  is the latest release ({info['latest']})"))
        return 0

    print(ui.bold("update available") +
          ui.dim(f"   {cur}  ->  ") + ui.bold(str(info.get("latest"))))
    print(ui.dim(f"  {info.get('url') or updatemod.HOMEPAGE}"))
    asset = info.get("asset")
    if not asset:
        print(ui.red("no build for this machine in that release") +
              ui.dim(f"  ({info.get('asset_for_platform')})"))
        print(ui.dim("  download manually from the release page above"))
        return 1
    print(ui.dim(f"  {asset['name']}  ({asset['size'] / 1048576:.0f} MB)"))

    if args.check:
        print(ui.dim("  (--check: nothing downloaded)"))
        return 0

    if updatemod.install_root() is None:
        print()
        print(ui.red("this is a source checkout, not a packaged build") +
              ui.dim("  — nothing to replace."))
        print(ui.dim("  update with:  git pull && uv sync --extra app"))
        return 0

    if not args.yes:
        print()
        print(ui.dim("  this replaces the installed app and restarts it."))
        print(ui.dim("  re-run with --yes to proceed."))
        return 0

    try:
        res = updatemod.update(cfg.root, apply=True, relaunch=not args.no_relaunch,
                               progress=lambda m: print(ui.dim("  " + m)))
    except updatemod.UpdateError as e:
        print(ui.red(f"update failed: {e}"))
        return 1
    print(ui.bold(f"staged {res.get('staged_version')}"))
    if res.get("updated"):
        print(ui.dim("  a helper will swap it in and restart jobscout once you quit."))
    else:
        print(ui.dim(f"  staged at {res['staged']} (not applied)"))
    return 0


def cmd_backup(args) -> int:
    """Archive the workspace. Thin view over jobscout/backup.py."""
    cfg = load_config()
    dest = Path(args.to).expanduser() if args.to else backupmod.default_destination()

    if args.list:
        rows = backupmod.list_backups(dest)
        if not rows:
            print(ui.dim(f"no backups in {dest}"))
            return 0
        for r in rows:
            mb = f"{r['bytes'] / 1048576:>7.1f} MB"
            print(f"  {ui.dim(r['created'])}  {ui.cyan(mb)}  {r['path'].name}")
        print(ui.dim(f"{len(rows)} backup(s) in {dest}"))
        return 0

    try:
        res = backupmod.backup(cfg.root, dest, include_reports=not args.no_reports)
    except Exception as e:  # a backup that failed must say so, loudly
        print(ui.red(f"backup failed: {type(e).__name__}: {e}"))
        return 1

    c = res["counts"]
    mb = res["bytes"] / 1048576
    print(ui.bold("jobscout backup") + ui.dim(f"  →  {res['archive']}"))
    print(f"{ui.cyan(str(c['postings']))} postings · {c['marked']} marked · "
          f"last run #{c['last_run']} · {mb:.1f} MB")
    print(ui.dim(f"{len(res['entries'])} files: config.yaml, jobscout.db, reports/"))
    print(ui.dim(f"restore: tar xzf {res['archive'].name} -C ~/.jobscout"))
    return 0


def cmd_config(args) -> int:
    cfg = load_config()
    print(cfg.path)
    if args.open:
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(cfg.path)], check=False)
        else:
            print("(--open is macOS-only; use the path above)")
    if args.edit:
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
        if editor:
            subprocess.run([editor, str(cfg.path)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-t", str(cfg.path)], check=False)
        else:
            print("(set $EDITOR to open automatically; use the path above)")
    return 0


def cmd_scan(args) -> int:
    """Render a scan. The loop itself lives in `scanner.scan` so the desktop
    app runs the identical code path — this function is only the view."""
    cfg = load_config()
    only = set(args.only.split(",")) if args.only else set()
    fetcher = _fetcher()
    conn = dbm.connect(cfg.root / "jobscout.db")
    quiet = args.quiet

    def on_event(e: dict) -> None:
        kind = e["kind"]
        if kind == "rescored":
            print(ui.bold("preferences changed") +
                  f" since run #{e['previous_run']}: rescored {e['total']} postings "
                  f"({e['changed']} changed)")
            print(f"  your profile now matches {ui.cyan(str(e['matches']))} stored postings · "
                  f"{ui.gold(str(e['strong']))} strong")
        elif kind == "start":
            print(ui.bold(f"jobscout scan · run #{e['run_id']}") +
                  ui.dim(f"  ·  {e['count']} sources"))
        elif kind == "pruned":
            print(f"  {ui.dim(e['name'])} {ui.dim('pruned')} {ui.red(str(e['removed']))} "
                  f"{ui.dim('unverifiable row(s), no publisher expiry')}")
        elif kind == "not_enabled":
            for n in e["names"]:
                print(ui.red(f"  {n} is not enabled") +
                      ui.dim("  — add it to sources.enabled, or tick it in "
                             "Settings; scanning it was skipped"))
        elif kind == "source_done" and not quiet:
            fetched_c = ui.dim(f"{e['fetched']:>4} fetched")
            new_c = ui.green(f"{e['new']:>3} new") if e["new"] else ui.dim(f"{e['new']:>3} new")
            mat_c = ui.cyan(f"{e['matches']:>3} matches") if e["matches"] else ui.dim(f"{e['matches']:>3} matches")
            print(f"  {e['name']:<15s} {fetched_c}  {new_c}  {mat_c}")

    def post_sources() -> dict | None:
        # liveness verification must land inside the run, before it is closed
        counts = _verify_on_scan(cfg, conn, fetcher, quiet=quiet)
        return {"verified": counts} if counts else None

    summary = scanner.scan(cfg, conn, fetcher, only=only, on_event=on_event,
                           post_sources=post_sources)

    min_score, strong_th = summary["min_score"], summary["strong_threshold"]
    if summary["matches_top"]:
        print("\n" + ui.rule())
        print(ui.bold("top new matches"))
        for pid, sc, title, company, loc in summary["matches_top"][:8]:
            meta = " · ".join(x for x in (company[:30], loc[:26]) if x)
            badge = ui.score_text(f"{sc:5g}", sc, min_score, strong_th)
            tail = ui.dim("  " + meta) if meta else ""
            print(f"  {ui.dim(f'#{pid}')}  {badge}  {title[:58]}{tail}")
    print("\n" + ui.rule())
    print(f"{ui.bold(str(summary['new']))} new · {ui.cyan(str(summary['matches']))} matches this run · "
          f"{summary['total_matches']} matching overall · {ui.gold(str(summary['strong']))} strong")
    if summary["errors"]:
        # Print the messages, not just a count. A keyed source that is enabled
        # without a key reports "0 fetched" and one line of "N source error(s)"
        # -- which is indistinguishable from a source that simply had no jobs
        # today, and leaves the user with no idea what to do. The actionable
        # text already exists in each message; hiding it in the digest footer
        # was the mistake.
        print(ui.red(f"{len(summary['errors'])} source error(s)"))
        for msg in summary["errors"][:6]:
            text = str(msg)
            # Generous cap, and the tail is what matters: these messages end
            # with the URL that fixes the problem, so a tight head-only cut
            # removes the actionable half.
            print("  " + ui.dim(text if len(text) <= 300 else text[:297] + "…"))
        extra = len(summary["errors"]) - 6
        if extra > 0:
            print(ui.dim(f"  … and {extra} more (the digest footer lists all)"))
    if summary["cancelled"]:
        print(ui.dim("scan cancelled — completed sources were saved"))
    if summary["unmarked"]:
        print(ui.dim(f"next: jobscout triage   ({summary['unmarked']} unmarked matches)   ·   "
                     f"jobscout digest --all"))
    else:
        print(ui.dim("next: jobscout digest --all    (everything matching)   ·   "
                     "jobscout digest   (this run)"))
    return 0
def _print_top(top, min_score: float, strong_th: float) -> None:
    for pid, title, company, score in top:
        badge = ui.score_text(f"{score:5g}", score, min_score, strong_th)
        tail = ui.dim("  · " + company[:30]) if company else ""
        print(f"  {ui.dim(f'#{pid}')}  {badge}  {title[:58]}{tail}")


def cmd_digest(args) -> int:
    cfg = load_config()
    conn = dbm.connect(cfg.root / "jobscout.db")
    min_score = float(cfg.prefs.get("min_score", 6))
    strong_th = float(cfg.prefs.get("strong_threshold", 12))
    if args.all:
        path, summary = digestmod.write_matches_digest(cfg, conn, top=args.top)
        print(ui.bold("jobscout matches") + ui.dim(f"  →  {path}"))
        print(f"{ui.cyan(str(summary['matches']))} match your profile · {ui.gold(str(summary['strong']))} strong")
        _print_top(summary["top"], min_score, strong_th)
        if args.open:
            subprocess.run(["open", str(path)], check=False)
        return 0
    path, summary = digestmod.write_digest(cfg, conn, run_id=args.run, top=args.top)
    print(ui.bold("jobscout digest") + ui.dim(f"  →  {path}"))
    print(f"run #{summary['run_id']} · {ui.bold(str(summary['new']))} new · "
          f"{ui.cyan(str(summary['matches']))} matches ({ui.gold(str(summary['strong']))} strong) · "
          f"{summary['filtered']} filtered · {summary['dups']} duplicates")
    _print_top(summary["top"], min_score, strong_th)
    if args.open:
        subprocess.run(["open", str(path)], check=False)
    return 0


def cmd_list(args) -> int:
    cfg = load_config()
    conn = dbm.connect(cfg.root / "jobscout.db")
    if args.filtered:
        rows = dbm.list_postings(conn, only_filtered=True, exclude_filtered=False, limit=args.limit)
        for r in rows:
            rid = ui.dim(f"#{r['id']}")
            src = ui.dim(f"  [{r['source']}]")
            print(f"  {rid}  {r['title'][:56]}  {ui.red(r['filtered_reason'])}{src}")
        print(ui.dim(f"\n{len(rows)} filtered · reasons show why they stay out of digests"))
        return 0
    min_score = args.min_score if args.min_score is not None else float(cfg.prefs.get("min_score", 6))
    strong_th = float(cfg.prefs.get("strong_threshold", 12))
    goal = None if getattr(args, "all_goals", False) else _active_goal(cfg, getattr(args, "goal", None))
    rows = dbm.list_postings(conn, min_score=min_score, status=args.status,
                             source=args.source, limit=100000 if goal else args.limit)
    if goal:
        # a goal only narrows the base profile; it never resurrects a filtered row
        rows = [r for r in rows if goalsmod.match(r, goal)][:args.limit]
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return 0
    if not rows:
        if goal:
            print(ui.dim(f"nothing matches goal '{goal.name}' ({goal.describe()})"))
            print(ui.dim(f"  {goal_note_full(goal)}"))
            print(ui.dim(f"  see the full pool: jobscout list --all-goals"))
        else:
            print(ui.dim("nothing here — run `jobscout scan` first, or loosen the filters"))
        return 0
    for r in rows:
        meta = " · ".join(x for x in (r["company"], r["location"], salmod.fmt_row(r)) if x)
        marks = []
        if goal:
            marks.append(ui.cyan("🎯"))
        if r["status"] != "new":
            marks.append(ui.magenta(f"[{r['status']}]"))
        if (r["verify_status"] or "") == "dead":
            marks.append(ui.red("[dead]"))
        status = " " + " ".join(marks) if marks else ""
        rid = ui.dim(f"#{r['id']}")
        badge = ui.score_text(f"{r['score']:5g}", r["score"], min_score, strong_th)
        tail = ui.dim(f"  {meta[:62]}") if meta else ""
        print(f"  {rid}  {badge}{status}  {r['title'][:58]}{tail}")
    if goal:
        print(ui.dim(f"\n{len(rows)} shown for goal '{goal.name}' · "
                     f"`--all-goals` shows the full pool"))
        return 0
    print(ui.dim(f"\n{len(rows)} shown · min score {min_score:g} · strong ≥ {strong_th:g} · "
                 f"`jobscout list --filtered` shows the rejects"))
    return 0


def cmd_show(args) -> int:
    cfg = load_config()
    conn = dbm.connect(cfg.root / "jobscout.db")
    row = dbm.get_posting(conn, args.id)
    if row is None:
        print(f"no posting #{args.id}")
        return 1
    desc = row["description"] or ""
    if args.refresh or len(desc) < 400:
        fetched = _fetch_detail(cfg, conn, row)
        if fetched:
            desc = fetched
    if args.json:
        try:
            reasons = json.loads(row["reasons"] or "[]")
        except ValueError:
            reasons = []
        print(json.dumps({
            "id": row["id"], "title": row["title"], "company": row["company"],
            "location": row["location"], "url": row["url"], "source": row["source"],
            "posted_at": row["posted_at"], "status": row["status"], "score": row["score"],
            "filtered": bool(row["filtered"]), "filtered_reason": row["filtered_reason"],
            "reasons": reasons, "compensation": row["compensation"], "description": desc,
            "salary": {"min": row["salary_min"], "max": row["salary_max"],
                       "currency": row["salary_currency"], "period": row["salary_period"],
                       "annual_max": row["salary_annual_max"], "raw": row["salary_raw"]},
            "liveness": {"status": row["verify_status"], "note": row["verify_note"],
                         "checked_at": row["verified_at"]},
        }, ensure_ascii=False, indent=2))
        return 0
    min_score = float(cfg.prefs.get("min_score", 6))
    strong_th = float(cfg.prefs.get("strong_threshold", 12))
    print(ui.bold(f"#{row['id']}  {row['title']}"))
    badge = ui.score_text(f"score {row['score']:g}", row["score"], min_score, strong_th)
    print("  " + badge + ui.dim(f" · {row['source']} · status {row['status']}"))
    if row["filtered"]:
        print("  " + ui.red(f"filtered: {row['filtered_reason']}"))
    live = ""
    if row["verify_status"]:
        live = row["verify_status"]
        if row["verify_note"]:
            live += f" → {row['verify_note']}"
        if row["verified_at"]:
            live += f" · checked {row['verified_at'][:16].replace('T', ' ')}"
    for label, value in (("company", row["company"]), ("location", row["location"]),
                         ("posted", row["posted_at"]), ("salary", salmod.fmt_row(row)),
                         ("compensation", row["compensation"]), ("liveness", live),
                         ("url", row["url"])):
        if value:
            print("  " + ui.dim(f"{(label + ':'):<13s}") + str(value))
    try:
        reasons = json.loads(row["reasons"] or "[]")
    except ValueError:
        reasons = []
    if reasons:
        print("  " + ui.dim("why:".ljust(13)) + " · ".join(f"{lab} +{pts:g}" for lab, pts in reasons))
    print("\n" + ui.rule() + "\n")
    print(desc[:6000] if desc else ui.dim("(no description available)"))
    return 0


def _fetch_detail(cfg, conn, row) -> str | None:
    mod = REGISTRY.get(row["source"])
    fetcher = _fetcher()
    p = Posting(source=row["source"], title=row["title"], company=row["company"],
                location=row["location"], url=row["url"], external_id=row["external_id"])
    desc = None
    try:
        fn = getattr(mod, "fetch_detail", None) if mod else None
        if fn is not None:
            desc = fn(p, fetcher)
        elif row["url"].startswith("http"):
            desc = detailmod.html_detail(row["url"], fetcher)
    except Exception as e:
        print(f"(detail fetch failed: {type(e).__name__}: {e})", file=sys.stderr)
    if desc and len(desc) > len(row["description"] or ""):
        dbm.set_description(conn, row["id"], desc)
        return desc
    return None


def cmd_mark(args) -> int:
    if args.status not in STATUSES:
        print(f"status must be one of: {', '.join(STATUSES)}")
        return 2
    cfg = load_config()
    conn = dbm.connect(cfg.root / "jobscout.db")
    row = dbm.get_posting(conn, args.id)
    if row is None:
        print(f"no posting #{args.id}")
        return 1
    dbm.set_status(conn, args.id, args.status)
    print(f"#{args.id} -> {args.status}")
    return 0


def _open_url(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", url], check=False)
    else:
        print(url)


def _read_key() -> str:
    """One keypress from a real terminal (no Enter needed)."""
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def _read_line() -> str:
    line = input().strip()
    return line[:1].lower() if line else ""


def _flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _fetcher() -> Fetcher:
    """Fetcher with certifi's CA bundle when available.

    europa.eu (EURES) serves a GlobalSign chain that the macOS trust store does
    not carry, so the default context refuses the connection. Prefer adding the
    root over disabling verification; every other host works unchanged.
    """
    try:
        import certifi
        import ssl
        return Fetcher(ssl_context=ssl.create_default_context(cafile=certifi.where()))
    except ImportError as e:
        # Do not fail silently: hosts like europa.eu will then error per request.
        print(f"(certifi unavailable, TLS verification falls back to the system "
              f"store: {e})", file=sys.stderr)
        return Fetcher()


def _row_expires(row) -> str:
    """Publisher-declared expiry for a postings row, or "" if the store
    predates the column. sqlite3.Row supports mapping access, but a plain
    tuple or a narrower SELECT would not, so stay defensive."""
    try:
        return row["expires_at"] or ""
    except (IndexError, KeyError, TypeError):
        return ""


def _verify_rows(conn, fetcher, rows, quiet: bool = False) -> tuple[dict, list]:
    counts = {"ok": 0, "dead": 0, "unknown": 0}
    dead = []
    for r in rows:
        # expires_at is a publisher-declared date, so sources behind a bot
        # challenge still get a real verdict. The column may be absent on a
        # store created before the migration, hence the .get fallback.
        state, note = liveness.check(fetcher, r["url"], _row_expires(r))
        dbm.update_verify(conn, r["id"], state, note)
        counts[state] += 1
        if state == "dead":
            dead.append((r["id"], r["title"]))
        if not quiet:
            rid = ui.dim(f"#{r['id']}")
            detail = ui.dim(f"  ({note})") if note else ""
            paint = {"dead": ui.red, "ok": ui.green, "unknown": ui.dim}[state]
            print(f"  {rid}  {paint(f'{state:>7s}')}  {r['title'][:52]}{detail}")
    return counts, dead


def _verify_on_scan(cfg, conn, fetcher, quiet: bool = False) -> dict | None:
    prefs = cfg.prefs
    if not _flag(prefs.get("verify_on_scan", True)):
        return None
    rows = dbm.postings_to_verify(
        conn, statuses=("saved", "applied"),
        max_age_days=int(float(prefs.get("verify_max_age_days", 3))),
        limit=int(float(prefs.get("verify_per_scan", 15))))
    if not rows:
        return None
    counts, dead = _verify_rows(conn, fetcher, rows, quiet=True)
    if not quiet:
        print("\n" + ui.bold("liveness") + ui.dim(f"  ·  checked {len(rows)} saved/applied  ·  ")
              + ui.green(f"{counts['ok']} ok") + " · " + ui.red(f"{counts['dead']} dead")
              + " · " + ui.dim(f"{counts['unknown']} unknown"))
        for pid, title in dead:
            print("  " + ui.red(f"⚠ #{pid} looks dead") + ui.dim(f"  {title[:56]}"))
    return counts


def cmd_verify(args) -> int:
    cfg = load_config()
    conn = dbm.connect(cfg.root / "jobscout.db")
    if args.recheck:
        # Re-probe rows whose previous verdict was inconclusive. `--all` cannot
        # do this: it orders by verified_at, so it hands you thousands of
        # already-ok rows before reaching the ones you actually wanted to
        # retry, and there is no way to name "unknown" today.
        #
        # `pending` is an alias for the empty string a never-verified row
        # carries. Without it the query matches nothing and reports "nothing to
        # verify", which is exactly the confusion it is meant to prevent.
        raw = [s.strip() for s in args.recheck.split(",") if s.strip()]
        states = [""] if "pending" in raw else []
        states += [s for s in raw if s != "pending"]
        if not states:
            print(ui.dim("no states given"))
            return 1
        marks = ",".join("?" * len(states))
        rows = conn.execute(
            f"SELECT * FROM postings WHERE url <> '' AND COALESCE(verify_status,'') IN ({marks}) "
            f"ORDER BY verified_at LIMIT ?", (*states, args.limit)).fetchall()
    elif args.all:
        rows = conn.execute("SELECT * FROM postings WHERE url <> '' ORDER BY verified_at LIMIT ?",
                            (args.limit,)).fetchall()
    else:
        statuses = tuple(s.strip() for s in (args.status or "saved,applied").split(",") if s.strip())
        rows = dbm.postings_to_verify(conn, statuses=statuses, max_age_days=0, limit=args.limit)
    if not rows:
        print(ui.dim("nothing to verify — no saved/applied postings yet "
                     "(`jobscout mark <id> saved` marks one)"))
        return 0
    fetcher = _fetcher()
    print(ui.bold(f"verifying {len(rows)} posting(s)"))
    counts, dead = _verify_rows(conn, fetcher, rows, quiet=args.quiet)
    print("\n" + ui.rule())
    print(f"checked {len(rows)} · {ui.green(str(counts['ok']) + ' ok')} · "
          f"{ui.red(str(counts['dead']) + ' dead')} · {counts['unknown']} unknown")
    if dead:
        print(ui.red("dead: ") + " ".join(f"#{i}" for i, _ in dead))
        print(ui.dim("  dismiss with: jobscout mark <id> dismissed"))
    return 0


def cmd_triage(args) -> int:
    cfg = load_config()
    conn = dbm.connect(cfg.root / "jobscout.db")
    min_score = args.min_score if args.min_score is not None else float(cfg.prefs.get("min_score", 6))
    strong_th = float(cfg.prefs.get("strong_threshold", 12))
    goal = None if getattr(args, "all_goals", False) else _active_goal(cfg, getattr(args, "goal", None))
    rows = [r for r in dbm.list_postings(conn, min_score=min_score, status="new",
                                         source=args.source, limit=100000 if goal else args.limit)
            if not r["filtered"]]
    if goal:
        rows = [r for r in rows if goalsmod.match(r, goal)][:args.limit]
    if not rows:
        if goal:
            print(ui.dim(f"no unmarked matches for goal '{goal.name}' ({goal.describe()})"))
            print(ui.dim(f"  {goal_note_full(goal)}"))
            print(ui.dim(f"  the full pool: jobscout triage --all-goals"))
        else:
            print(ui.dim("no unmarked matches to review — run `jobscout scan`, or see `jobscout list`"))
        return 0
    if goal:
        print(ui.cyan(f"🎯 goal: {goal.name}") + ui.dim(f"  ({goal.describe()})")
              + ui.dim(f"  ·  --all-goals for the full pool"))
    if sys.stdin.isatty():
        reader = _read_key
        print(ui.dim("single keys · o or Enter = open · s = saved · a = applied · "
                     "x = dismiss · n = next · q = quit"))
    else:
        reader = _read_line
    counts = triagemod.run(rows, decide=lambda pid, st: dbm.set_status(conn, pid, st),
                           opener=_open_url, reader=reader,
                           min_score=min_score, strong_th=strong_th)
    print("\n" + ui.rule())
    print(triagemod.summary_text(counts))
    if not counts["quit"]:
        if goal:
            left = sum(1 for r in dbm.list_postings(conn, min_score=min_score, status="new",
                                                   limit=100000)
                       if not r["filtered"] and goalsmod.match(r, goal))
            if left:
                print(ui.dim(f"{left} unmarked goal match(es) left — run `jobscout triage` again"))
            else:
                print(ui.dim("that was the last one for this goal · `jobscout goal off` "
                             "returns to the full pool"))
            return 0
        left = conn.execute(
            "SELECT COUNT(*) n FROM postings WHERE status='new' AND filtered=0 "
            "AND dup_of IS NULL AND score >= ?", (min_score,)).fetchone()["n"]
        if left:
            print(ui.dim(f"{left} unmarked match(es) left — run `jobscout triage` again"))
        else:
            print(ui.dim("that was the last unmarked match · `jobscout list --status saved` "
                         "shows your shortlist"))
    return 0


def cmd_discover(args) -> int:
    """Point the liveness checker at a platform API instead of a walled page."""
    conn = dbm.connect(load_config().root / "jobscout.db")
    q = ("SELECT url FROM postings WHERE filtered=0 AND dup_of IS NULL "
         "AND url IS NOT NULL AND url<>''")
    params: list = []
    if args.source:
        q += " AND source=?"
        params.append(args.source)
    rows = [r["url"] for r in conn.execute(q + " LIMIT ?", (*params, args.limit))]
    conn.close()

    if not rows:
        print(ui.dim("no URLs to test"))
        return 0

    results = [discover.probe(u) for u in rows]
    ok = [r for r in results if r["ok"]]
    for r in results:
        mark = ui.green("ok  ") if r["ok"] else ui.dim("fail")
        print(f"  {mark} {ui.dim((r['platform'] or '-')[:16].ljust(16))} "
              f"{ui.dim((r.get('mode') or '-')[:10].ljust(10))} {r.get('why','')}")

    by: dict[str, list[bool]] = {}
    for r in results:
        by.setdefault(r["platform"] or "unmatched", []).append(r["ok"])
    print()
    for plat, v in sorted(by.items(), key=lambda kv: -sum(kv[1])):
        print(f"  {plat:<18} {sum(v)}/{len(v)}")

    # Feed the per-item platforms we proved out into liveness, so a recheck
    # stops probing the walled page. Membership platforms need no change: their
    # branch already checks feed membership rather than fetching the page.
    usable = sorted({r["platform"] for r in ok if r["mode"] == "per-item"})
    if usable:
        print()
        print(ui.dim(f"per-item API verified for: {', '.join(usable)}"))
    print(f"\n{len(ok)}/{len(results)} verifiable without touching a human check")
    return 0


def cmd_add_company(args) -> int:
    cfg = load_config()
    fetcher = _fetcher()
    url = args.url if args.url.startswith("http") else "https://" + args.url
    cands = companiesmod.detect("", url)
    if not cands:
        try:
            status, body = fetcher.get(url)
        except FetchError as e:
            print(ui.red(f"could not fetch {url}: {e.message}"))
            return 1
        if status != 200:
            print(ui.red(f"could not fetch {url} (HTTP {status})"))
            return 1
        cands = companiesmod.detect(body, url)
    if not cands:
        print(ui.red("no Greenhouse / Ashby / Lever / Workable / SmartRecruiters / "
                     "Teamtailor board found on that page"))
        print(ui.dim("  hint: open the careers page, click into a role, and pass the URL "
                     "of the job listing itself"))
        return 1
    print(ui.dim("candidates: ") + ", ".join(f"{s}/{t}" for s, t in cands))
    for src, tok in cands:
        ok, detail = companiesmod.validate(fetcher, src, tok)
        mark = ui.green("ok") if ok else ui.red("no")
        print(f"  {src:<15s} {tok:<24s} {mark}  {ui.dim(detail)}")
        if ok:
            status = companiesmod.add_token(cfg.path, src, tok)
            if status == "exists":
                print(ui.dim(f"already in your config: {src}/{tok}"))
            else:
                print(ui.bold(f"added {src}/{tok} to {cfg.path}") + ui.dim(f"  ({status})"))
            enabled = companiesmod.enable_source(cfg.path, src)
            if enabled == "added":
                print(ui.dim(f"also enabled the `{src}` source (it was not in sources.enabled)"))
            elif enabled == "missing":
                print(ui.dim(f"note: no sources.enabled list found — make sure `{src}` is enabled"))
            print(ui.dim(f"  take it in with: jobscout scan --only {src}"))
            return 0
    print(ui.red("none of the candidates validated — nothing added"))
    return 1


def cmd_rescore(args) -> int:
    cfg = load_config()
    conn = dbm.connect(cfg.root / "jobscout.db")
    total, changed = dbm.rescore_all(conn, cfg.prefs)
    m, s = dbm.count_matches(conn, float(cfg.prefs.get("min_score", 6)),
                             float(cfg.prefs.get("strong_threshold", 12)))
    print(f"rescored {total} postings, {changed} changed")
    print(f"matches under your current profile: {m} ({s} strong) — see: jobscout digest --all")
    return 0


def cmd_stats(args) -> int:
    cfg = load_config()
    conn = dbm.connect(cfg.root / "jobscout.db")
    print(ui.bold("postings per source"))
    for src, n in dbm.counts(conn).items():
        print(f"  {src:15s} {n:>5}")
    status_rows = conn.execute("SELECT status, COUNT(*) n FROM postings GROUP BY status ORDER BY n DESC").fetchall()
    print("\n" + ui.dim("status ") + " · ".join(f"{r['status']} {r['n']}" for r in status_rows))
    hist = dbm.score_histogram(conn)
    if hist:
        min_score = float(cfg.prefs.get("min_score", 6))
        strong_th = float(cfg.prefs.get("strong_threshold", 12))
        m, s = dbm.count_matches(conn, min_score, strong_th)
        print("\n" + ui.bold("score distribution") + ui.dim(
            f"  ·  matches ≥ {min_score:g}: {m}   strong ≥ {strong_th:g}: {s}"))
        for value, n in hist:
            if value >= strong_th:
                paint = ui.gold
            elif value >= min_score:
                paint = ui.cyan
            else:
                paint = ui.dim
            print(f"  {value:>3}  {n:>4}  {paint('█' * max(1, round(n / 4)))}")
    print("\n" + ui.bold("runs"))
    for r in dbm.recent_runs(conn):
        stats = {}
        try:
            stats = json.loads(r["stats"] or "{}")
        except ValueError:
            pass
        news = sum(s.get("new", 0) for s in (stats.get("sources") or {}).values())
        print(f"  #{r['id']:<4d} {_local(r['started_at'])} · {news} new · {stats.get('requests', 0)} requests"
              + (f" · {len(stats.get('errors') or [])} errors" if stats.get("errors") else ""))
    return 0


def _active_goal(cfg, override=None):
    """The active goal, or None. Goals are always optional."""
    if override is not None:
        if override == "":
            return None
        g = cfg.goals.get(override)
        if g is None:
            print(ui.red(f"no goal named '{override}' — see `jobscout goals`"))
            return None
        return g
    if not cfg.active_goal:
        return None
    return cfg.goals.get(cfg.active_goal)


def _goal_note(cfg, g) -> str:
    return ui.dim(f"  goal: {g.name} ({g.describe()})") if g else ""


def goal_note_full(g) -> str:
    """One-line detail of what a goal actually requires, for the empty-state hint."""
    bits = []
    if g.kind == "location" and g.locations:
        bits.append("location contains " + ", ".join(g.locations))
    if g.kind == "salary" and g.salary_min:
        bits.append(f"annualised salary ≥ {g.salary_min:,.0f} EUR")
    if g.terms:
        bits.append("text contains " + ", ".join(g.terms))
    if g.min_score is not None:
        bits.append(f"base score ≥ {g.min_score:g}")
    return "; ".join(bits) or "no conditions set"


def cmd_goals(args) -> int:
    cfg = load_config()
    conn = dbm.connect(cfg.root / "jobscout.db")
    goals = cfg.goals
    if not goals:
        print(ui.dim("no goals defined — add a `goals:` block to your config"))
        print(ui.dim("  each goal is ONE standalone target, e.g. austria / senior / "
                     "a salary floor — not a combination"))
        print(ui.dim("  example:\n"
                     "    goals:\n"
                     "      active: austria\n"
                     "      austria:\n"
                     "        kind: location\n"
                     "        locations: [austria, österreich, wien, graz, linz]\n"
                     "        label: Austria"))
        return 0
    counts = goalsmod.counts_by_goal(conn, goals)
    active = cfg.active_goal
    print(ui.bold("goals") + (ui.dim("  (one active at a time — it is the default view)")
                              if active else ui.dim("  (none active — showing everything)")))
    for name, g in goals.items():
        mark = ui.cyan("→ ") if name == active else "  "
        n = counts.get(name, 0)
        onoff = "" if g.enabled else ui.dim(" (disabled)")
        print(f"{mark}{ui.bold(name):<14} {g.describe()}{onoff}  {ui.dim(str(n) + ' match(es)')}")
    print(ui.dim(f"\nactivate: jobscout goal use <name>   ·   deactivate: jobscout goal off"))
    return 0


def cmd_goal(args) -> int:
    cfg = load_config()
    if not cfg.goals:
        print(ui.red("no goals defined — add a `goals:` block to your config first"))
        return 1
    # `jobscout goal use austria` and `jobscout goal austria` are both accepted
    action = (args.action or "use").lower()
    name = args.name
    if action in ("use", "on", "set", "activate") and name is None and args.action != "use":
        name, action = args.action, "use"     # bare `goal austria` form
    if args.off or args.clear:
        action, name = "off", None
    if action == "off":
        cfg.active_goal = ""
    elif action in ("use", "on", "set", "activate"):
        if not name:
            print(ui.red("which goal? — `jobscout goal use <name>`, or `jobscout goals` to list"))
            return 1
        if name not in cfg.goals:
            print(ui.red(f"no goal named '{name}' — see `jobscout goals`"))
            return 1
        cfg.active_goal = name
    else:
        print(ui.red(f"unknown action '{action}' — use `jobscout goal use <name>` or `jobscout goal off`"))
        return 1
    # persist: rewrite the `active:` key inside the goals block.
    #
    # This used to be a raw re.sub with the replacement r"\1active: \"\"". In a
    # replacement TEMPLATE `\"` is not an escape re understands, so it emitted a
    # literal backslash and the file ended up with `active: \"\"` -- which YAML
    # reads back as a 4-character TRUTHY string. jobscout then believed a goal
    # named \"\" was active: `goals` claimed one was, while no goal filtered.
    #
    # cfgwrite owns config.yaml edits (line-level, comment-preserving, atomic),
    # so it owns this one too.
    from . import cfgwrite
    try:
        cfgwrite.edit(cfg.path, "scalar", "goals.active", cfg.active_goal)
    except cfgwrite.ConfigEditError:
        # No `goals:` block at all: there is nothing to activate into, and the
        # list above already told the user goals do not exist.
        pass
    if action == "off":
        print(ui.dim("goal mode off — showing the full pool again"))
    else:
        g = cfg.goals[cfg.active_goal]
        print(ui.cyan(f"goal active: {g.name}") + ui.dim(f"  ({g.describe()})"))
        print(ui.dim("list / triage now default to this goal; "
                     "`--all-goals` shows the full pool"))
    return 0


def _abbrev(path: str) -> str:
    """~/.jobscout instead of /Users/someone/.jobscout."""
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except ValueError:
        return str(path)


def cmd_profile(args) -> int:
    from . import profiles
    action = (args.action or "list").lower()
    name = (args.name or "").strip()

    if action in ("list", "ls", "show"):
        rows = profiles.list_profiles()
        print(ui.bold("profiles") + ui.dim(
            "  (each is a separate workspace: its own config, database and reports)"))
        for r in rows:
            mark = ui.cyan("→ ") if r["active"] else "  "
            n = r["postings"]
            count = f"{n:,} postings" if isinstance(n, int) else "empty"
            print(f"{mark}{ui.bold(r['label']):<20} {ui.dim(count):<28} "
                  f"{ui.dim(_abbrev(r['root']))}")
        print(ui.dim("\nswitch: jobscout profile use <name>   ·   "
                     "create: jobscout profile create <name>"))
        return 0

    # `Default` is spelled with a capital in the list, but accept any casing
    if name.lower() == profiles.DEFAULT_LABEL.lower():
        name = ""

    if action in ("use", "switch", "on"):
        if not name:
            print(ui.red("which profile? — `jobscout profile list` shows them"))
            return 1
        if profiles.find(name) is None:
            print(ui.red(f"no profile called '{name}' — see `jobscout profile list`"))
            return 1
        profiles.write_active(name)
        row = profiles.find(name)
        print(ui.cyan(f"now using: {row['label']}") + ui.dim(f"  ({_abbrev(row['root'])})"))
        return 0

    if action in ("create", "new", "add"):
        problem = profiles.name_problem(name)
        if problem:
            print(ui.red(problem))
            return 1
        try:
            root = profiles.create(name)
        except ValueError as e:
            print(ui.red(str(e)))
            return 1
        print(ui.cyan(f"created profile '{name}'") + ui.dim(f"  ({_abbrev(root)})"))
        print(ui.dim("it starts empty, with the default config and no postings."))
        print(ui.dim(f"switch to it with: jobscout profile use {name}"))
        return 0

    if action in ("remove", "rm", "delete"):
        if not args.yes:
            print(ui.red(f"this deletes the profile '{name}' and its database."))
            print(ui.dim("a backup is taken first. add --yes to go ahead:"))
            print(ui.dim(f"    jobscout profile remove {name} --yes"))
            return 1
        try:
            archive = profiles.remove(name)
        except ValueError as e:
            print(ui.red(str(e)))
            return 1
        print(ui.cyan(f"removed profile '{name}'"))
        print(ui.dim(f"safety copy kept at {_abbrev(str(archive))}"))
        print(ui.dim("restore it by unpacking into that profile's folder."))
        return 0

    print(ui.red(f"unknown action '{action}' — use list, use, create or remove"))
    return 1


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="jobscout",
                                     description="local-first job search (no API keys, no accounts)")
    parser.add_argument("--version", action="version", version=f"jobscout {__version__}")
    parser.add_argument("--no-color", action="store_true",
                        help="disable colored output (NO_COLOR is honored too)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create ~/.jobscout with a default config")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("config", help="print the config path (--open reveals it in Finder, --edit opens it)")
    p.add_argument("--open", action="store_true")
    p.add_argument("--edit", action="store_true")
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("restore", help="put a backup back into the workspace")
    p.add_argument("--from", dest="from_archive", metavar="FILE",
                   help="the backup to restore (default: the newest one)")
    p.add_argument("--to", metavar="DIR",
                   help="where the backups live (default: ~/jobscout-backups)")
    p.add_argument("--overwrite", action="store_true",
                   help="replace an existing workspace (a safety copy is kept)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("update", help="check for and install a newer build")
    p.add_argument("--check", action="store_true",
                   help="only report whether an update exists")
    p.add_argument("--yes", action="store_true", help="actually install it")
    p.add_argument("--from", dest="from_archive", metavar="FILE",
                   help="install a build you already downloaded (zip or tar.gz)")
    p.add_argument("--stage-only", action="store_true",
                   help="with --from: unpack but do not swap it in")
    p.add_argument("--no-relaunch", action="store_true",
                   help="do not restart jobscout after installing")
    p.add_argument("--prerelease", action="store_true",
                   help="consider prereleases too")
    p.set_defaults(fn=cmd_update)

    p = sub.add_parser("backup",
                       help="archive the workspace (config, database, reports) to one file")
    p.add_argument("--to", metavar="DIR",
                   help="destination directory (default: ~/jobscout-backups)")
    p.add_argument("--list", action="store_true", help="list existing backups and exit")
    p.add_argument("--no-reports", action="store_true", help="leave the reports/ directory out")
    p.set_defaults(fn=cmd_backup)

    p = sub.add_parser("scan", help="fetch all enabled sources, score, store new postings")
    p.add_argument("--only", help="comma-separated source names to run (e.g. stepstone,xing)")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("digest", help="write a markdown digest for a run (default: latest)")
    p.add_argument("--run", type=int, help="run id (default: latest)")
    p.add_argument("--all", action="store_true",
                   help="all stored matches under the current profile (not just the latest run)")
    p.add_argument("--top", type=int, help="cap entries per section")
    p.add_argument("--open", action="store_true", help="open the digest (macOS)")
    p.set_defaults(fn=cmd_digest)

    p = sub.add_parser("list", help="list stored matches")
    p.add_argument("--min-score", type=float, dest="min_score")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--source")
    p.add_argument("--filtered", action="store_true",
                   help="show postings that were filtered out, with reasons")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--json", action="store_true")
    p.add_argument("--goal", help="view this goal instead of the active one")
    p.add_argument("--all-goals", action="store_true", dest="all_goals",
                   help="ignore the active goal and show the full pool")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("goals", help="list defined goals with live match counts")
    p.set_defaults(fn=cmd_goals)

    p = sub.add_parser("goal", help="activate or deactivate the one active goal")
    p.add_argument("action", nargs="?", default="use",
                   help="'use <name>' to activate, or pass a bare goal name")
    p.add_argument("name", nargs="?", help="goal to activate")
    p.add_argument("--off", action="store_true", help="deactivate (full pool again)")
    p.add_argument("--clear", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_goal)

    p = sub.add_parser("profile",
                       help="switch between separate workspaces (profiles)")
    p.add_argument("action", nargs="?", default="list",
                   help="list (default), use <name>, create <name>, remove <name>")
    p.add_argument("name", nargs="?")
    p.add_argument("--yes", action="store_true",
                   help="confirm removing a profile and its database")
    p.set_defaults(fn=cmd_profile)

    p = sub.add_parser("show", help="show one posting (fetches the full description if needed)")
    p.add_argument("id", type=int)
    p.add_argument("--refresh", action="store_true", help="re-fetch the description")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output, includes the stored description text")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("triage", help="walk unmarked matches one by one (o/s/a/x keys)")
    p.add_argument("--limit", type=int, default=25, help="how many to review (default 25)")
    p.add_argument("--min-score", type=float, dest="min_score")
    p.add_argument("--source")
    p.add_argument("--goal", help="review this goal instead of the active one")
    p.add_argument("--all-goals", action="store_true", dest="all_goals",
                   help="ignore the active goal and review the full pool")
    p.set_defaults(fn=cmd_triage)

    p = sub.add_parser("verify", help="check saved/applied postings for dead listings")
    p.add_argument("--status", help="comma list of statuses (default: saved,applied)")
    p.add_argument("--all", action="store_true", help="every stored posting with a URL")
    p.add_argument("--recheck",
                   help="re-probe rows whose last verdict was one of these "
                        "liveness states, oldest first (e.g. unknown, pending). "
                        "Wins over --all.")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("add-company",
                       help="find the ATS board behind a careers page (Greenhouse/Ashby/Lever/"
                            "Workable/SmartRecruiters/Teamtailor) and add it")
    p.add_argument("url", help="careers page or job-listing URL")
    p.set_defaults(fn=cmd_add_company)

    p = sub.add_parser("discover",
                       help="for blocked job URLs, find and test a public API to "
                            "verify liveness against (stdin, one URL per line)")
    p.add_argument("--source", help="only URLs from this source")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(fn=cmd_discover)

    p = sub.add_parser("mark", help="set a posting's status")
    p.add_argument("id", type=int)
    p.add_argument("status")
    p.set_defaults(fn=cmd_mark)

    p = sub.add_parser("rescore", help="re-score all stored postings with the current preferences")
    p.set_defaults(fn=cmd_rescore)

    p = sub.add_parser("stats", help="counts and recent runs")
    p.set_defaults(fn=cmd_stats)

    args = parser.parse_args(argv)
    ui.init(force_off=args.no_color)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
