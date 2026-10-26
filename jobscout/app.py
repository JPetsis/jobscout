"""Desktop shell: renders the jobscout UI in the OS-native webview.

The front-end is a client of `jobscout/api.py`; it never sees a rule. This
module only moves plain data between the window and the service layer.

Threading: pywebview invokes `js_api` methods from worker threads, so every DB
touch holds `dbm.LOCK` (see the note above LOCK in db.py).
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from . import api, backup as backupmod, db as dbm, scanner, update as updatemod
from .config import load_config
from .http import Fetcher

WEB = Path(__file__).resolve().parent / "web"


class RestoreOffer:
    """The API behind the first-run window that offers a backup back.

    Exists because of one specific afternoon: a deep-uninstaller deleted
    ~/.jobscout, and the app on next launch said only "no config found, run
    jobscout init". A backup was sitting right there in ~/jobscout-backups and
    nothing connected the two.
    """

    def __init__(self, root, backups) -> None:
        self.root = root
        self.backups = backups
        self.windows: list = []
        self.done = False

    def info(self) -> dict:
        rows = []
        for b in self.backups[:5]:
            d = backupmod.describe(b["path"])
            rows.append({"name": b["path"].name, "path": str(b["path"]),
                         "created": d.get("created") or b.get("created", ""),
                         "postings": d.get("postings"), "marked": d.get("marked")})
        return {"root": str(self.root), "backups": rows}

    def restore(self, path: str = "") -> dict:
        target = Path(path) if path else self.backups[0]["path"]
        try:
            res = backupmod.restore(target, self.root, overwrite=False)
        except Exception as e:                                 # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self.done = True
        return {"ok": True, "counts": res["counts"], "archive": str(res["archive"])}

    def quit(self) -> dict:
        """Close the window once the restore is done.

        Scheduled rather than immediate, and for the same reason as JsApi.quit:
        destroying the window from inside a js_api call leaves pywebview's own
        non-daemon call thread unable to hand back its result, and the process
        then never exits.
        """
        import threading

        def _close() -> None:
            for w in list(self.windows):
                try:
                    w.destroy()
                except Exception:                              # noqa: BLE001
                    pass

        threading.Timer(0.3, _close).start()
        return {"ok": True}


_RESTORE_HTML = """<!doctype html><meta charset="utf-8">
<style>
 body{margin:0;padding:26px 28px;background:#16171a;color:#ececf0;
      font:13.5px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
 h1{font-size:15px;margin:0 0 6px}
 p{margin:0 0 12px;color:#9a9aa2}
 ul{list-style:none;margin:0 0 16px;padding:0}
 li{background:#1e1f23;border:1px solid #32343a;border-radius:10px;
    padding:10px 12px;margin-bottom:8px;cursor:pointer}
 li.on{border-color:#2b5cd9}
 li b{font-weight:600}
 .meta{color:#9a9aa2;font-size:12px}
 .row{display:flex;gap:9px;align-items:center}
 button{font:inherit;border-radius:8px;padding:7px 14px;cursor:pointer;
        border:1px solid #32343a;background:#1e1f23;color:#ececf0}
 button.primary{background:#2b5cd9;border-color:#2b5cd9;color:#fff}
 button:disabled{opacity:.5;cursor:default}
 .ok{color:#3fb950}.err{color:#d29922}
</style>
<h1>jobscout has no profile here yet</h1>
<p id="lead">Loading backups&hellip;</p>
<ul id="list"></ul>
<div class="row">
  <button class="primary" id="go" disabled>Restore</button>
  <button id="skip">Start fresh</button>
</div>
<p id="result" class="meta"></p>
<script>
let CHOSEN = null, DATA = null;

function fmt(n, what) { return n == null ? what : n + ' ' + what; }

async function boot() {
  DATA = await pywebview.api.info();
  const lead = document.getElementById('lead');
  const list = document.getElementById('list');
  if (!DATA.backups.length) {
    lead.textContent = 'No backup was found, so there is nothing to restore.';
    document.getElementById('skip').textContent = 'Close';
    return;
  }
  lead.textContent = DATA.backups.length + ' backup(s) found in ~/jobscout-backups. '
    + 'Restore one, or start fresh.';
  DATA.backups.forEach((b, i) => {
    const li = document.createElement('li');
    const bits = [fmt(b.postings, 'postings'), fmt(b.marked, 'marked')].join(' \u00b7 ');
    li.innerHTML = '<b>' + b.name + '</b><div class="meta">' + (b.created || '')
      + ' &middot; ' + bits + '</div>';
    li.onclick = () => {
      CHOSEN = b.path;
      document.getElementById('go').disabled = false;
      document.getElementById('go').textContent = 'Restore ' + b.name;
      [...list.children].forEach((c) => c.classList.remove('on'));
      li.classList.add('on');
    };
    if (i === 0) li.onclick();          // preselect the newest
    list.appendChild(li);
  });
}

document.getElementById('go').onclick = async () => {
  const btn = document.getElementById('go');
  btn.disabled = true;
  const res = await pywebview.api.restore(CHOSEN || '');
  const out = document.getElementById('result');
  if (res && res.ok) {
    out.className = 'ok';
    out.textContent = 'Restored ' + res.counts.postings + ' postings, '
      + res.counts.marked + ' marked. Close this window and open jobscout again.';
    document.getElementById('skip').textContent = 'Close';
  } else {
    out.className = 'err';
    out.textContent = 'Could not restore: ' + ((res && res.error) || 'unknown error');
    btn.disabled = false;
  }
};
document.getElementById('skip').onclick = () => pywebview.api.quit();

/* Wait for the bridge, exactly as the main window does -- `pywebview.api` is
 * not injected when an end-of-body script first runs. The direct call covers
 * the opposite ordering, where the bridge won the race. */
if (window.pywebview && window.pywebview.api) { boot(); }
else { window.addEventListener('pywebviewready', boot); }
</script>
"""


class ScanJob:
    """Runs one scan on a worker thread and buffers its events for polling.

    SQLite: this opens its OWN connection rather than sharing the UI's. Two
    connections to one file is what sqlite is built for; sharing one connection
    across threads is not, even with check_same_thread=False. The busy timeout
    makes the UI's reads wait for the scan's write instead of erroring.

    Events are buffered and drained by the front-end rather than pushed: a
    js_api method called mid-scan cannot deliver a stream, so the front-end
    polls. Draining (rather than replaying) keeps the payload bounded on a
    2,000-posting scan.
    """

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.running = False
        self.summary: dict | None = None
        self.error: str | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, only: set[str] | None = None, force: bool = False) -> dict:
        if self.running:
            return {"started": False, "reason": "a scan is already running"}
        self.events.clear()
        self.summary = None
        self.error = None
        self._cancel.clear()
        self.running = True
        # NOTE: args must be a TUPLE. `args=(only or set())` passes the set
        # itself, so threading unpacks it as positional args and _run gets
        # called with nothing (or worse, with strings it never expected).
        self._thread = threading.Thread(target=self._run, args=(only or set(), force),
                                        daemon=True, name="jobscout-scan")
        self._thread.start()
        return {"started": True}

    def cancel(self) -> dict:
        if not self.running:
            return {"cancelled": False, "reason": "nothing running"}
        self._cancel.set()
        return {"cancelled": True}

    def _emit(self, event: dict) -> None:
        with self.lock:
            # cap the buffer so a pathological source cannot grow it without end
            if len(self.events) < 5000:
                self.events.append(event)

    def _run(self, only: set[str], force: bool = False) -> None:
        conn = None
        try:
            conn = dbm.connect(self.cfg.root / "jobscout.db")
            conn.execute("PRAGMA busy_timeout = 30000")
            delay = float(self.cfg.prefs.get("host_delay", 0.6) or 0)
            summary = scanner.scan(self.cfg, conn, Fetcher(host_delay=delay), only=only,
                                   on_event=self._emit, cancel=self._cancel.is_set,
                                   force=force)
            with self.lock:
                self.summary = summary
        except Exception as e:                                   # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
        finally:
            if conn is not None:
                conn.close()
            self.running = False

    def drain(self) -> dict:
        with self.lock:
            events, self.events = self.events, []
            return {"running": self.running, "events": events,
                    "summary": self.summary, "error": self.error}


SCAN: ScanJob | None = None


class JsApi:
    """Everything the front-end may call. Plain data in, plain data out."""

    def __init__(self) -> None:
        self.cfg = load_config()
        self.conn = dbm.connect(self.cfg.root / "jobscout.db")
        # filled by main(); quit() closes these so the update helper -- which
        # waits for this process to exit -- can swap the build in.
        self._windows: list = []
        self._staged: str | None = None          # a downloaded, verified build
        self._staged_version: str | None = None
        self._asked = False                      # see launch()
        self._picker_shown = False                # see boot_scan()
        self._first_run = False                   # set when this run made the workspace

    def matches(self, limit: int | None = None) -> list[dict]:
        """Matches under the stored profile, best first. Filtering is client-side.

        The count comes from `preferences.max_results` rather than a constant in
        the window, because a constant is a silent ceiling: the grid showed 400
        of 1,294 matches and 59 of the 71 freshest postings -- the ones this app
        exists to surface -- were simply not in the payload.
        """
        if limit is None:
            limit = int(self.cfg.prefs.get("max_results", 2000))
        with dbm.LOCK:
            return api.list_matches(
                self.conn,
                min_score=float(self.cfg.prefs.get("min_score", 6)),
                limit=int(limit),
            )

    def stats(self) -> dict:
        with dbm.LOCK:
            return api.stats(self.conn, self.cfg.prefs)

    def sources(self) -> list[dict]:
        with dbm.LOCK:
            return api.source_counts(self.conn)

    def mark(self, pid: int, status: str) -> dict:
        with dbm.LOCK:
            return api.mark_posting(self.conn, int(pid), str(status))

    def detail(self, pid: int, refresh: bool = False) -> dict:
        """Everything the detail pane shows. Fetches live text when asked."""
        with dbm.LOCK:
            return api.show_posting(self.conn, self.cfg, int(pid),
                                    refresh=bool(refresh)) or {}

    # ------------------------------------------------------- job logger

    def application(self, pid: int) -> dict:
        """The application for one posting, its history included."""
        with dbm.LOCK:
            return api.get_application(self.conn, int(pid)) or {}

    def save_application(self, pid: int, fields: dict) -> dict:
        with dbm.LOCK:
            return api.save_application(self.conn, int(pid), dict(fields or {}))

    def add_application_note(self, pid: int, text: str) -> dict:
        with dbm.LOCK:
            return api.add_application_note(self.conn, int(pid), str(text or "")) or {}

    def delete_application(self, pid: int) -> dict:
        with dbm.LOCK:
            return api.delete_application(self.conn, int(pid))

    def applications(self, open_only: bool = False) -> list[dict]:
        with dbm.LOCK:
            return api.list_applications(self.conn, open_only=bool(open_only))

    def application_stats(self) -> dict:
        with dbm.LOCK:
            return api.application_stats(self.conn)

    def settings(self) -> list[dict]:
        return api.read_settings(self.cfg.path)

    def save_setting(self, key: str, value) -> dict:
        """Write one setting, then reload so scoring/filters see it at once."""
        out = api.write_setting(self.cfg.path, str(key), value)
        if out.get("changed"):
            self.cfg = load_config()          # re-read; the file just changed
        return out

    # ------------------------------------------------- companies you follow

    def watchlist(self) -> list[dict]:
        """The per-company boards this profile follows, and what they bring."""
        return api.watchlist(self.cfg)

    def watch_add(self, url: str) -> dict:
        """Follow the employer behind a careers URL, if the board checks out."""
        out = api.watch_add(self.cfg, str(url or ""))
        if out.get("ok"):
            self.cfg = load_config()          # the config was just edited
        return out

    def watch_remove(self, board: str, token: str) -> dict:
        out = api.watch_remove(self.cfg, str(board or ""), str(token or ""))
        if out.get("ok"):
            self.cfg = load_config()
        return out

    def theme(self) -> str:
        """The stored colour scheme: system | light | dark."""
        return api.read_theme(self.cfg.path)

    def set_theme(self, mode: str) -> str:
        """Persist the colour scheme. Nothing needs reloading: the palette is
        resolved in the page, and no Python side reads ui.theme."""
        return api.write_theme(self.cfg.path, str(mode))

    # ------------------------------------------------------------ profiles

    def launch(self) -> dict:
        """What the window needs to decide whether to ask who is using it.

        The question is asked once per run, and only when there is a choice to
        make: with a single profile it would be a screen nobody needs to click
        through. `_asked` is what stops it reappearing after a switch, because
        switching reloads the page and the page asks again.
        """
        from . import profiles as profmod
        rows = profmod.list_profiles()
        # A single profile is not a choice worth a screen, with one exception:
        # the run that just created the workspace. Naming your own workspace is
        # the first decision a person should get to make, and it is cheaper here
        # than renaming it later.
        ask = (len(rows) > 1 or self._first_run) and not self._asked
        self._asked = True
        # boot_scan must not fill a workspace the user has not chosen yet
        self._picker_shown = ask
        return {"ask": ask, "profiles": rows,
                "active": profmod.read_active()}

    def switch_profile(self, name: str) -> dict:
        """Point the window at another workspace.

        Refused while a scan is running: ScanJob holds its own config and its
        own connection, so a switch mid-scan would have it writing into the
        profile you just left.
        """
        from . import profiles as profmod
        global SCAN
        if SCAN is not None and SCAN.running:
            return {"ok": False, "error": "a scan is running — wait for it or cancel it"}
        name = str(name or "")
        if profmod.find(name) is None:
            return {"ok": False, "error": f"no profile called '{name}'"}
        profmod.write_active(name)
        with dbm.LOCK:
            try:
                self.conn.close()
            except Exception:                      # noqa: BLE001
                pass                               # already closed is fine
            self.cfg = load_config()
            self.conn = dbm.connect(self.cfg.root / "jobscout.db")
        SCAN = None                                # the next scan uses the new profile
        return {"ok": True, "active": profmod.read_active(),
                "root": str(self.cfg.root), "label": profmod.find(name)["label"]}

    def create_profile(self, name: str, activate: bool = True) -> dict:
        """Make a new, empty profile and optionally start using it."""
        from . import profiles as profmod
        clean = str(name or "").strip()
        problem = profmod.name_problem(clean)
        if problem:
            return {"ok": False, "error": problem}
        try:
            profmod.create(clean)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not activate:
            return {"ok": True, "created": clean, "activated": False}
        # Reuse the switch path rather than repeating the rebind: creating a
        # profile and switching to it must leave the window in exactly the
        # state switching alone would.
        out = self.switch_profile(clean)
        out["created"] = clean
        return out

    def start_scan(self, only: str = "", force: bool = True) -> dict:
        """Start a scan. True (the button) re-fetches even fresh sources.

        The startup scan passes force=False and respects scan_if_older_hours;
        a person pressing Scan means now, not "unless you did this recently".
        """
        global SCAN
        if SCAN is None:
            SCAN = ScanJob(self.cfg)
        return SCAN.start({s for s in only.split(",") if s} if only else set(), force=force)

    def salary_report(self, words: str = "", city: str = "", days: int = 0) -> dict:
        """What the postings in this board advertise, with the caveats attached."""
        terms = [w for w in str(words or "").split() if w.strip()]
        if not terms and not city:
            # default to the profile's own titles: the useful question is about
            # the jobs this person would take, not about the whole corpus
            terms = [str(t) for t in (self.cfg.prefs.get("title_terms") or [])][:3]
        with dbm.LOCK:
            return api.salary_report(self.conn, words=terms, city=str(city or ""),
                                     days=int(days or 0))

    def search_text(self, text: str = "") -> dict:
        """Look inside the advert text, which is most of what is stored here.

        The count of results is the configured one, so a search can never see
        less of the corpus than the grid does. The first call in a store builds
        the index (a second or two); every call after it is milliseconds.
        """
        limit = int(self.cfg.prefs.get("max_results", 2000) or 2000)
        with dbm.LOCK:
            return api.search_text(self.conn, str(text or ""), limit=limit)

    def boot_scan(self) -> dict:
        """Look for new openings when the window opens, if the last look is old.

        Everything time-based in this app is inert without regular scans: "fresh
        from the source" is only meaningful if somebody looks daily, the
        lead-time measurement stays a curiosity, and a role being re-advertised
        is invisible. Until now that only happened when a person pressed Scan.

        This is the cheap, cross-platform half of running by itself -- no
        scheduler and no daemon, just a look on open. Two refusals matter:

        * never while the profile picker is on screen. A profile is a whole
          workspace, and scanning before the user has chosen would fill
          whichever one happened to be current;
        * never when a scan is already running.
        """
        from . import db as dbm
        global SCAN
        if getattr(self, "_picker_shown", False):
            return {"started": False, "reason": "waiting for you to pick a profile"}
        if SCAN is not None and SCAN.running:
            return {"started": False, "reason": "already scanning"}
        hours = float(self.cfg.prefs.get("scan_if_older_hours", 24) or 0)
        if hours <= 0:
            return {"started": False, "reason": "automatic scans are off"}
        since = None
        with dbm.LOCK:
            run = dbm.get_run(self.conn)
        if run is not None:
            stamp = run["finished_at"] or run["started_at"]
            try:
                when = datetime.fromisoformat(str(stamp))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                since = (datetime.now(timezone.utc) - when).total_seconds() / 3600.0
            except (ValueError, TypeError):
                since = None       # unreadable timestamp: scan rather than assume
        if since is not None and since < hours:
            return {"started": False, "reason": f"last scan was {since:.1f}h ago",
                    "hours": round(since, 1)}
        started = self.start_scan("", force=False)
        started["auto"] = True
        started["hours"] = None if since is None else round(since, 1)
        return started

    def scan_progress(self) -> dict:
        if SCAN is None:
            return {"running": False, "events": [], "summary": None, "error": None}
        return SCAN.drain()

    def cancel_scan(self) -> dict:
        return SCAN.cancel() if SCAN is not None else {"cancelled": False}

    def app_info(self) -> dict:
        """Which build this is, for the window to show.

        Deliberately offline and never raising: it answers "which version am I
        running" and "where does the update log live" without touching the
        network. `frozen` matters because a source checkout cannot install an
        update at all, and saying so is better than a button that silently
        cannot work.
        """
        try:
            return {
                "version": updatemod.current_version(),
                "frozen": updatemod.install_root() is not None,
                "log": str(self.cfg.root / "update.log"),
            }
        except Exception:                                      # noqa: BLE001
            return {"version": updatemod.current_version(),
                    "frozen": False, "log": ""}

    def update_check(self, force: bool = False) -> dict:
        """Is there a newer build? Cached for a day unless forced.

        `force` means a person pressed the button, so it ignores BOTH the cache
        and the `check: false` setting -- the same rule as `jobscout update`
        from a terminal. Asking for a check is the instruction; the setting is
        about what the app does on its own.

        `auto` tells the front-end whether to install a found update without
        asking, so the decision lives in the config rather than being hardcoded
        in the window.
        """
        if not force and not updatemod.checks_allowed(self.cfg):
            return {"ok": True, "disabled": True, "update_available": False,
                    "current": updatemod.current_version()}
        try:
            info = updatemod.check(self.cfg.root, force=bool(force))
        except Exception as e:                                 # noqa: BLE001
            updatemod.log(self.cfg.root, f"check raised: {type(e).__name__}: {e}")
            return {"ok": False, "error": f"{type(e).__name__}: {e}",
                    "update_available": False}
        info["auto"] = updatemod.auto_install(self.cfg)
        return info

    def update_install(self) -> dict:
        """Download and verify the update and STOP there.

        Deliberately split from update_quit: the download is safe to do while
        the app runs, but installing means terminating jobscout, and that is the
        user's decision to make explicitly rather than something that happens
        because they pressed a button labelled "install".

        A build staged earlier is reused, so choosing "not now" and coming back
        does not re-download 45 MB.
        """
        if updatemod.install_root() is None:
            return {"ok": False,
                    "error": "running from a source checkout; update with "
                             "git pull && uv sync --extra app"}
        staged = getattr(self, "_staged", None)
        if staged and Path(staged).exists():
            return {"ok": True, "updated": False, "staged": str(staged),
                    "staged_version": getattr(self, "_staged_version", None),
                    "reused": True}
        try:
            res = updatemod.update(self.cfg.root, apply=False)
        except updatemod.UpdateError as e:
            updatemod.log(self.cfg.root, f"install failed: {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:                                 # noqa: BLE001
            # Anything unexpected would otherwise reject the bridge promise and
            # reach the window as an unexplained failure. Say what it was.
            updatemod.log(self.cfg.root, f"install raised: {type(e).__name__}: {e}")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        # The signal that there is something to install is `staged`. NOT
        # `updated`: that one means "swapped in", and this call deliberately
        # does not swap -- with apply=False it is always False. Reading it as
        # "nothing to do" threw away every download and made the install path
        # a no-op that reported "already up to date" while an update waited.
        staged = res.get("staged")
        if not staged:
            return {"ok": True, "updated": False, "staged": None,
                    "reason": res.get("reason", "already up to date")}
        self._staged = str(staged)
        self._staged_version = res.get("staged_version")
        return {"ok": True, "updated": True, "staged": str(staged),
                "staged_version": res.get("staged_version")}

    def update_quit(self) -> dict:
        """Terminate jobscout so the staged build can replace it.

        Only called after the user explicitly asks to quit AND install. The
        helper is spawned first (it outlives us), then the window is closed, so
        the process actually exits and the swap can happen.
        """
        staged = getattr(self, "_staged", None)
        if not staged or not Path(staged).exists():
            updatemod.log(self.cfg.root,
                          "quit: nothing staged, so there is nothing to swap")
            return {"ok": False, "error": "nothing has been staged yet"}
        try:
            updatemod.apply_staged(Path(staged), relaunch=True,
                                   logpath=self.cfg.root / "update.log")
        except updatemod.UpdateError as e:
            updatemod.log(self.cfg.root, f"quit: could not start the swap: {e}")
            return {"ok": False, "error": str(e)}
        updatemod.log(self.cfg.root, "quit: helper started, closing the window")
        self.quit()
        return {"ok": True, "quitting": True}

    def quit(self) -> dict:
        """Close the window so the updater helper can do its swap.

        Both halves of "called from a js_api handler" matter here:

        * pywebview runs each bridge call on its own NON-daemon thread, and
          that thread is still waiting to hand the return value back. Destroying
          the window from inside the call therefore leaves the process alive for
          ever, and the helper waits its full 120 seconds, gives up, and the
          update silently does nothing. Reproduced in isolation: start() returns
          and the interpreter STILL cannot exit, because the call thread is
          stuck replying to a webview that no longer exists.
        * so the destroy is scheduled for after this returns, and a hard exit
          backstops it in case anything else holds the process open (a scan
          running, say). A helper is waiting for this pid: it has to go.
        """
        import os
        import threading

        def _close() -> None:
            for w in list(self._windows):
                try:
                    w.destroy()
                except Exception:                              # noqa: BLE001
                    pass
            # Non-daemon on purpose. If the teardown did not end the process,
            # this is what stops the helper waiting for a window that will
            # never close.
            threading.Timer(3.0, lambda: os._exit(0)).start()

        threading.Timer(0.3, _close).start()
        return {"ok": True}

    def open_url(self, url: str) -> dict:
        """Open in the real browser -- a job posting belongs in a browser tab."""
        if url:
            webbrowser.open(str(url))
        return {"ok": bool(url)}


WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def _app_dir() -> Path:
    """The folder the running app lives in.

    Frozen, that is next to the .exe; from source it is the package directory.
    Anything the bundle carries has to be looked up this way, never from the
    working directory: a user who double-clicks the app has no working directory
    we control.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _webview2_bootstrapper() -> Path | None:
    """The Evergreen Bootstrapper shipped inside this build, when there is one."""
    for name in ("MicrosoftEdgeWebView2Setup.exe",
                 "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"):
        candidate = _app_dir() / name
        if candidate.is_file():
            return candidate
    return None


def _ask_windows(question: str) -> bool:
    """A Yes/No box with no GUI toolkit and no console.

    A windowed build has no terminal, so printing a URL is the same as saying
    nothing at all. ctypes is part of Python on Windows and needs no dependency.
    """
    try:
        import ctypes                                        # noqa: PLC0415
        mb_yesno, mb_iconwarning, id_yes = 0x4, 0x30, 6
        win = ctypes.windll.user32                            # type: ignore[attr-defined]
        return win.MessageBoxW(None, question, "jobscout",
                               mb_yesno | mb_iconwarning) == id_yes
    except Exception:
        return False


def _tell_windows(message: str) -> None:
    """Show a failure in a box, for builds that have nowhere to print."""
    try:
        import ctypes                                        # noqa: PLC0415
        mb_ok, mb_iconerror = 0x0, 0x10
        ctypes.windll.user32.MessageBoxW(                     # type: ignore[attr-defined]
            None, message, "jobscout", mb_ok | mb_iconerror)
    except Exception:
        pass


def _install_webview2(bootstrapper: Path) -> bool:
    """Offer to install the runtime, then re-check.

    The bootstrapper is Microsoft's own, redistributed with the app for exactly
    this case: a Windows Server or LTSC image, where Edge (and therefore
    WebView2) is not part of the OS. One click here beats sending a
    non-technical user to a developer page to pick the right download.
    """
    if not _ask_windows(
            "jobscout needs the Microsoft Edge WebView2 runtime to draw its "
            "window.\n\n"
            "This Windows edition does not include it, but this copy of "
            "jobscout brought the installer with it.\n\n"
            "Install it now? It takes about a minute and needs an internet "
            "connection."):
        return False
    try:
        subprocess.run([str(bootstrapper), "/silent", "/install"],
                       check=False, timeout=900)
    except (OSError, subprocess.SubprocessError):
        return False
    return _has_webview2()


#: System library -> the package that provides it. Only the libraries seen in
#: practice, because a table of guesses is worse than naming the file.
_LINUX_SO_PACKAGES = {
    "libegl.so.1": ("libegl1", "mesa-libEGL"),
    "libgl.so.1": ("libgl1", "mesa-libGL"),
    "libgbm.so.1": ("libgbm1", "mesa-libgbm"),
    "libminizip.so.1": ("libminizip1", "minizip-compat"),
    "libopus.so.0": ("libopus0", "opus"),
    "libnss3.so": ("libnss3", "nss"),
    "libxkbcommon.so.0": ("libxkbcommon0", "libxkbcommon"),
    "libxcb-cursor.so.0": ("libxcb-cursor0", "xcb-util-cursor"),
    "libfontconfig.so.1": ("libfontconfig1", "fontconfig"),
}


def _missing_shared_library(exc: BaseException) -> str:
    """The .so named by an ImportError, or '' when the error is not about one."""
    match = re.search(r"(lib[\w.+\-]+\.so(?:\.\d+)*)", str(exc))
    return match.group(1) if match else ""


def _missing_library_message(lib: str) -> str:
    deb, rpm = _LINUX_SO_PACKAGES.get(lib.lower(), ("", ""))
    hint = ""
    if deb:
        hint = ("\n\nInstall it and start jobscout again:\n"
                f"    Debian/Ubuntu:  sudo apt install {deb}\n"
                f"    Fedora:         sudo dnf install {rpm or deb}")
    return (f"jobscout could not load its bundled Qt, because this system "
            f"library is missing:\n    {lib}\n"
            f"Qt itself is included in this build; {lib} belongs to your "
            f"distribution.{hint}\n\n"
            "A normal desktop has this already. A minimal or container "
            "install usually does not.")


def _linux_backend_problem() -> str | None:
    """Why the Qt backend will not load, phrased as something to do.

    Checking the package name is not enough: PyQt6 can be present and still
    fail to import because one of its SYSTEM libraries is absent, and pywebview
    then reports "install QT or GTK" -- sending the user to install the one
    thing the bundle definitely carries. Importing QtWebEngine, which is what
    pywebview itself does, names the missing file.
    """
    try:
        importlib.import_module("qtpy")
    except ImportError as exc:
        lib = _missing_shared_library(exc)
        if lib:
            return _missing_library_message(lib)
        return ('jobscout needs a GUI backend on Linux:\n'
                '    pip install "pywebview[qt]"\n'
                "A packaged build already carries Qt; this only applies to a "
                "source install.")
    try:
        importlib.import_module("qtpy.QtWebEngineWidgets")
    except ImportError as exc:
        lib = _missing_shared_library(exc)
        if lib:
            return _missing_library_message(lib)
        return ("jobscout could not load Qt WebEngine, which this build "
                "carries. Run it from a terminal to see the underlying "
                "ImportError, and report it with that exact message.")
    return None


def _check_backend() -> None:
    """Fail with a fixable message when the OS renderer is missing.

    Every platform needs one specific thing that a plain `pip install` does not
    guarantee. Finding out at launch with a real message beats a blank window.
    """
    if sys.platform.startswith("linux"):
        # Chromium's sandbox refuses to start as root, with an error that names
        # --no-sandbox and nothing else. WSL sessions and containers are often
        # root, so say the useful thing before the crash says the cryptic one.
        # geteuid exists on every POSIX system and not on Windows, and the tests
        # fake this branch on every platform.
        geteuid = getattr(os, "geteuid", None)
        if geteuid is not None and geteuid() == 0 \
                and not os.environ.get("QTWEBENGINE_DISABLE_SANDBOX"):
            print("jobscout: running as root. Chromium's sandbox will refuse to "
                  "start; run as a normal user, or set "
                  "QTWEBENGINE_DISABLE_SANDBOX=1 if you accept that.",
                  file=sys.stderr)
        # pywebview has NO default Linux backend. GTK is only an option for a
        # source install; the bundle carries Qt, so that is the path to explain.
        try:
            importlib.import_module("gi")
        except ImportError:
            problem = _linux_backend_problem()
            if problem:
                raise SystemExit(problem)

    if sys.platform == "win32":
        # WebView2 ships with Windows 11 and 10 1803+, but NOT with Windows
        # Server or LTSC images, where the user has to install it once.
        if not _has_webview2():
            bootstrapper = _webview2_bootstrapper()
            if bootstrapper is not None and _install_webview2(bootstrapper):
                return
            message = (
                "jobscout needs the Microsoft Edge WebView2 runtime, which is "
                "not installed on this machine.\n\n"
                "It is a one-time install, and it is already present on "
                "Windows 11 and Windows 10 1803+.\n\n"
                f"Download it from {WEBVIEW2_URL} (choose the Evergreen "
                "Bootstrapper), then start jobscout again.")
            if bootstrapper is not None:
                message += (f"\n\nOr run the copy in this folder: "
                            f"{bootstrapper}")
            _tell_windows(message)
            raise SystemExit(message)


def _has_webview2() -> bool:
    """Is the WebView2 runtime present? Checks the machine and user scopes."""
    try:
        import winreg                                  # noqa: PLC0415
    except ImportError:                                # not Windows after all
        return True
    # this branch only ever runs on Windows; the module has these attributes
    # there, so the static analyser on macOS is reporting a false positive
    key = (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
           r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
    hklm = winreg.HKEY_LOCAL_MACHINE                          # type: ignore[attr-defined]
    hkcu = winreg.HKEY_CURRENT_USER                           # type: ignore[attr-defined]
    read = winreg.KEY_READ                                    # type: ignore[attr-defined]
    wow32 = getattr(winreg, "KEY_WOW64_32KEY", 0)
    for root, access in ((hklm, read | wow32), (hkcu, read)):
        try:
            with winreg.OpenKey(root, key, 0, access) as k:   # type: ignore[attr-defined]
                version, _ = winreg.QueryValueEx(k, "pv")     # type: ignore[attr-defined]
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def _show_fatal(webview, message: str) -> None:
    """Show a startup failure in a window.

    A windowed build has no console: `print()` and `SystemExit` go absolutely
    nowhere, so a missing config file looked to the user like the app simply
    refused to open. That is a dead end for the non-technical user this app is
    for, and it is the first thing they would hit on a fresh machine.
    """
    import html as _html
    body = _html.escape(message).replace("\n", "<br>")
    webview.create_window(
        "jobscout",
        html=(
            "<!doctype html><meta charset='utf-8'>"
            "<style>body{margin:0;padding:28px;background:#16171a;color:#ececf0;"
            "font:13.5px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}"
            "h1{font-size:15px;margin:0 0 14px}"
            "code{background:#232a3d;padding:2px 6px;border-radius:5px;"
            "font-family:ui-monospace,Menlo,monospace}"
            "pre{background:#232a3d;padding:12px;border-radius:8px;overflow-x:auto}"
            ".m{color:#9a9aa2}</style>"
            f"<h1>jobscout cannot start</h1><p>{body}</p>"
            "<p class='m'>Set this up once, then open jobscout again.</p>"
        ),
        width=600, height=340,
    )
    webview.start()


def main() -> int:
    _check_backend()
    try:
        import webview
    except ImportError:
        raise SystemExit(
            "jobscout-app needs the desktop UI dependencies:\n"
            '    uv pip install -e ".[app]"') from None

    try:
        api_obj = JsApi()
    except SystemExit as e:
        # Nothing to work with. Before saying so, check whether a backup is
        # sitting there -- a fresh workspace plus an existing backup is exactly
        # the state a user lands in after losing or removing their data.
        from . import profiles
        root = profiles.resolve_root()
        found = []
        if backupmod.workspace_is_empty(root):
            try:
                found = backupmod.list_backups(backupmod.default_destination())
            except OSError:
                found = []
        if found:
            offer = RestoreOffer(root, found)
            offer.windows.append(webview.create_window(
                "jobscout", html=_RESTORE_HTML, js_api=offer,
                width=620, height=460))
            webview.start()
            return 0
        # Nothing to restore either, so make the workspace instead of naming a
        # command this bundle does not contain. "Run jobscout init" is fine in a
        # terminal and useless in a downloaded app: the point of shipping
        # binaries is that somebody who has never opened a terminal can use it.
        # The core is identical either way, so the window creates what it needs
        # and then asks what to call the workspace.
        from .config import init_workspace
        try:
            init_workspace(root)
        except OSError as exc:
            # An unwritable home is a real problem, and it has to be named
            # rather than fail in silence.
            _show_fatal(webview, f"{e}\n\ncould not create it: {exc}")
            return 1
        try:
            api_obj = JsApi()
        except SystemExit as fatal:
            _show_fatal(webview, str(fatal))
            return 1
        api_obj._first_run = True

    api_obj._windows = [webview.create_window(
        "jobscout",
        url=str(WEB / "index.html"),
        js_api=api_obj,
        width=1180,
        height=860,
        min_size=(820, 560),
    )]
    # The window icon is only honoured by GTK. macOS takes its icon from the
    # bundle and Windows from the executable, both of which the build sets, so
    # passing it anywhere else would be a line that does nothing.
    icon = WEB / "icon.png"
    if sys.platform.startswith("linux") and icon.exists():
        webview.start(icon=str(icon))
    else:
        webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
