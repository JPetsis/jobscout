"""Desktop shell: renders the jobscout UI in the OS-native webview.

The front-end is a client of `jobscout/api.py`; it never sees a rule. This
module only moves plain data between the window and the service layer.

Threading: pywebview invokes `js_api` methods from worker threads, so every DB
touch holds `dbm.LOCK` (see the note above LOCK in db.py).
"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser
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
        for w in list(self.windows):
            try:
                w.destroy()
            except Exception:                                  # noqa: BLE001
                pass
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

    def start(self, only: set[str] | None = None) -> dict:
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
        self._thread = threading.Thread(target=self._run, args=(only or set(),),
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

    def _run(self, only: set[str]) -> None:
        conn = None
        try:
            conn = dbm.connect(self.cfg.root / "jobscout.db")
            conn.execute("PRAGMA busy_timeout = 30000")
            summary = scanner.scan(self.cfg, conn, Fetcher(), only=only,
                                   on_event=self._emit, cancel=self._cancel.is_set)
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

    def matches(self, limit: int = 400) -> list[dict]:
        """Matches under the stored profile, best first. Filtering is client-side."""
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

    def settings(self) -> list[dict]:
        return api.read_settings(self.cfg.path)

    def save_setting(self, key: str, value) -> dict:
        """Write one setting, then reload so scoring/filters see it at once."""
        out = api.write_setting(self.cfg.path, str(key), value)
        if out.get("changed"):
            self.cfg = load_config()          # re-read; the file just changed
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
        ask = len(rows) > 1 and not self._asked
        self._asked = True
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

    def start_scan(self, only: str = "") -> dict:
        global SCAN
        if SCAN is None:
            SCAN = ScanJob(self.cfg)
        return SCAN.start({s for s in only.split(",") if s} if only else set())

    def scan_progress(self) -> dict:
        if SCAN is None:
            return {"running": False, "events": [], "summary": None, "error": None}
        return SCAN.drain()

    def cancel_scan(self) -> dict:
        return SCAN.cancel() if SCAN is not None else {"cancelled": False}

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
        if not res.get("updated"):
            return {"ok": True, "updated": False, "staged": None,
                    "reason": res.get("reason", "already up to date")}
        self._staged = res["staged"]
        self._staged_version = res.get("staged_version")
        return {"ok": True, "updated": True, "staged": res["staged"],
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
        """Close the window so the updater helper can do its swap."""
        for w in list(self._windows):
            try:
                w.destroy()
            except Exception:                                  # noqa: BLE001
                pass
        return {"ok": True}

    def open_url(self, url: str) -> dict:
        """Open in the real browser -- a job posting belongs in a browser tab."""
        if url:
            webbrowser.open(str(url))
        return {"ok": bool(url)}


WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def _check_backend() -> None:
    """Fail with a fixable message when the OS renderer is missing.

    Every platform needs one specific thing that a plain `pip install` does not
    guarantee. Finding out at launch with a real message beats a blank window.
    """
    if sys.platform.startswith("linux"):
        # pywebview has NO default Linux backend.
        ok = False
        for mod in ("qtpy", "gi"):
            try:
                __import__(mod)
                ok = True
                break
            except ImportError:
                continue
        if not ok:
            raise SystemExit(
                "jobscout-app needs a GUI backend on Linux:\n"
                '    pip install "pywebview[qt]"\n'
                "(GTK also works but PyGObject must be compiled against system "
                "GTK headers.)")

    if sys.platform == "win32":
        # WebView2 ships with Windows 11 and 10 1803+, but NOT with Windows
        # Server or LTSC images, where the user has to install it once.
        if not _has_webview2():
            raise SystemExit(
                "jobscout-app needs the Microsoft Edge WebView2 runtime, which "
                "is not installed on this machine.\n"
                "It is a one-time install (it is already present on Windows 11 "
                "and on Windows 10 1803+):\n"
                f"    {WEBVIEW2_URL}\n"
                "Choose the Evergreen Bootstrapper, then start jobscout again.")


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
        # no console in a windowed build -- the message must be visible
        _show_fatal(webview, str(e))
        return 1

    api_obj._windows = [webview.create_window(
        "jobscout",
        url=str(WEB / "index.html"),
        js_api=api_obj,
        width=1180,
        height=860,
        min_size=(820, 560),
    )]
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
