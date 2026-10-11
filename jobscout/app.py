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

from . import api, db as dbm, scanner
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

    def open_url(self, url: str) -> dict:
        """Open in the real browser -- a job posting belongs in a browser tab."""
        if url:
            webbrowser.open(str(url))
        return {"ok": bool(url)}


def _check_backend() -> None:
    """pywebview has NO default Linux backend: fail with a fixable message."""
    if sys.platform.startswith("linux"):
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


def main() -> int:
    _check_backend()
    try:
        import webview
    except ImportError:
        raise SystemExit(
            "jobscout-app needs the desktop UI dependencies:\n"
            '    uv pip install -e ".[app]"') from None

    webview.create_window(
        "jobscout",
        url=str(WEB / "index.html"),
        js_api=JsApi(),
        width=1180,
        height=860,
        min_size=(820, 560),
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
