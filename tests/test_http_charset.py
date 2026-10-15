"""The Fetcher decodes with the charset the server declares.

myScience serves ISO-8859-15; a hard UTF-8 decode turned "Zürich" into
"Z�rich". These run against a local HTTP server so no network is needed.
"""
import http.server
import threading

from jobscout.http import Fetcher


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/iso":
            body = "Zürich größer".encode("iso-8859-15")
            ctype = "text/html; charset=iso-8859-15"
        else:
            body = "Zürich größer".encode("utf-8")
            ctype = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _serve():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


def test_declared_charset_is_honoured():
    srv, port = _serve()
    try:
        status, text = Fetcher(host_delay=0.0).get(f"http://127.0.0.1:{port}/iso")
        assert status == 200
        assert "Zürich" in text and "größer" in text
        assert "\ufffd" not in text
    finally:
        srv.shutdown()


def test_no_declaration_defaults_to_utf8():
    srv, port = _serve()
    try:
        status, text = Fetcher(host_delay=0.0).get(f"http://127.0.0.1:{port}/plain")
        assert status == 200
        assert "Zürich" in text and "größer" in text
        assert "\ufffd" not in text
    finally:
        srv.shutdown()
