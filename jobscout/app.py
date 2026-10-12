"""Desktop shell: renders the jobscout UI in the OS-native webview.

The front-end is a client of `jobscout/api.py`; it never sees a rule. This
module only moves plain data between the window and the service layer.

Threading: pywebview invokes `js_api` methods from worker threads, so every DB
touch holds `dbm.LOCK` (see the note above LOCK in db.py).
"""
from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path

from . import api, db as dbm, scanner, update as updatemod
from .config import load_config
from .http import Fetcher

WEB = Path(__file__).resolve().parent / "web"


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
        """Is there a newer build? Cached for a day unless forced."""
        if self.cfg.prefs.get("updates", {}).get("check") is False:
            return {"ok": True, "disabled": True, "update_available": False,
                    "current": updatemod.current_version()}
        try:
            return updatemod.check(self.cfg.root, force=bool(force))
        except Exception as e:                                 # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}",
                    "update_available": False}

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
            return {"ok": False, "error": str(e)}
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
            return {"ok": False, "error": "nothing has been staged yet"}
        try:
            updatemod.apply_staged(Path(staged), relaunch=True)
        except updatemod.UpdateError as e:
            return {"ok": False, "error": str(e)}
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
