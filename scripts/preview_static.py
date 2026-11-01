#!/usr/bin/env python3
"""Serve `jobscout/web` over HTTP so the chrome can be looked at in a browser.

This is a LAYOUT tool, not the app. It answers the pywebview bridge with empty
values, so the window paints with no postings, no counts and no profile list:
enough to see spacing, alignment, wrapping and colour, and nothing else. There
is no database behind it and no call it makes can change anything.

    python scripts/preview_static.py            # http://127.0.0.1:8782
    python scripts/preview_static.py --port 9000

For a preview with real postings in it, run the app itself.
"""
from __future__ import annotations

import argparse
import http.server
import socketserver
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "jobscout" / "web"

# The page calls pywebview.api.<method>() at load. Without a bridge it throws
# before painting, which hides the very layout this tool exists to show.
SHIM = """
<script>
window.pywebview = { api: new Proxy({}, {
  get: () => async () => ({})
}) };
</script>
"""

TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
         ".png": "image/png", ".ico": "image/x-icon"}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:                       # noqa: N802 (http.server API)
        rel = self.path.split("?", 1)[0].lstrip("/") or "index.html"
        target = (WEB / rel).resolve()
        if WEB not in target.parents and target != WEB:
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        if target.name == "index.html":
            body = body.replace(b"</head>", SHIM.encode() + b"</head>", 1)
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:   # noqa: A002 (http.server)
        pass                                          # keep the terminal quiet


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8782)
    args = ap.parse_args()
    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as srv:
        print(f"serving {WEB} on http://127.0.0.1:{args.port} (layout only, no data)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
