"""Desktop shell: renders the jobscout UI in the OS-native webview.

The front-end is a client of `jobscout/api.py`; it never sees a rule. This
module only moves plain data between the window and the service layer.

Threading: pywebview invokes `js_api` methods from worker threads, so every DB
touch holds `dbm.LOCK` (see the note above LOCK in db.py).
"""
from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

from . import api, db as dbm
from .config import load_config

WEB = Path(__file__).resolve().parent / "web"


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
