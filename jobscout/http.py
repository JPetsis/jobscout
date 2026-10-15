"""Polite HTTP client: browser UA, per-host pacing, retries, gzip, declared charsets.

Never raises for plain HTTP error statuses (callers inspect them); raises
FetchError only when the network itself failed after retries.
"""
from __future__ import annotations

import gzip
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class FetchError(Exception):
    def __init__(self, url: str, message: str):
        super().__init__(f"{url}: {message}")
        self.url = url
        self.message = message


class Fetcher:
    def __init__(self, host_delay: float = 0.6, timeout: float = 25.0, retries: int = 2,
                 user_agent: str = DEFAULT_UA, ssl_context: ssl.SSLContext | None = None):
        self.host_delay = host_delay
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent
        # europa.eu serves a GlobalSign chain that the macOS trust store lacks;
        # pass a certifi-built context there instead of disabling verification.
        self.ssl_context = ssl_context
        self.requests = 0
        self.errors: list[str] = []
        self._last: dict[str, float] = {}

    def _headers(self, extra: dict | None = None) -> dict:
        h = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip",
        }
        h.update(extra or {})
        return h

    def _wait(self, host: str, delay: float | None = None) -> None:
        d = self.host_delay if delay is None else delay
        last = self._last.get(host)
        if last is not None:
            wait = d - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
        self._last[host] = time.time()

    def get(self, url: str, headers: dict | None = None,
            host_delay: float | None = None) -> tuple[int, str]:
        host = urllib.parse.urlsplit(url).netloc
        h = self._headers(headers)
        attempt = 0
        while True:
            attempt += 1
            self._wait(host, host_delay)
            req = urllib.request.Request(url, headers=h)
            self.requests += 1
            try:
                with urllib.request.urlopen(req, timeout=self.timeout,
                                            context=self.ssl_context) as r:
                    data = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        data = gzip.decompress(data)
                    # Honour the charset the server declares: myScience
                    # serves ISO-8859-15, and decoding it as UTF-8 turned
                    # every umlaut into \ufffd. No declaration -> UTF-8,
                    # which is what the other boards and feeds speak.
                    charset = r.headers.get_content_charset() or "utf-8"
                    try:
                        text = data.decode(charset, "replace")
                    except LookupError:
                        text = data.decode("utf-8", "replace")
                    return r.status, text
            except urllib.error.HTTPError as e:
                body = b""
                try:
                    body = e.read()
                except Exception:
                    pass
                if e.code >= 500 and attempt <= self.retries:
                    time.sleep(1.5 * attempt)
                    continue
                return e.code, body.decode("utf-8", "replace")
            except Exception as e:  # URLError, timeout, ssl, ...
                if attempt <= self.retries:
                    time.sleep(1.5 * attempt)
                    continue
                self.errors.append(f"{host}: {type(e).__name__}: {e}")
                raise FetchError(url, f"{type(e).__name__}: {e}") from e

    def post_json(self, url: str, payload: dict, host_delay: float | None = None,
                  headers: dict | None = None):
        """POST a JSON body and parse the JSON response (same retry rules).

        `headers` mirrors get_json's parameter. It exists because some APIs
        localise their response from Accept-Language, and the default header
        here is German-first (correct for the German boards, wrong for an API
        whose location strings have to be matched by an English gazetteer).
        """
        host = urllib.parse.urlsplit(url).netloc
        extra = {"Content-Type": "application/json"}
        extra.update(headers or {})
        h = self._headers(extra)
        body = json.dumps(payload).encode("utf-8")
        attempt = 0
        while True:
            attempt += 1
            self._wait(host, host_delay)
            req = urllib.request.Request(url, data=body, method="POST", headers=h)
            self.requests += 1
            try:
                with urllib.request.urlopen(req, timeout=self.timeout,
                                            context=self.ssl_context) as r:
                    data = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        data = gzip.decompress(data)
                    status, text = r.status, data.decode("utf-8", "replace")
                    break
            except urllib.error.HTTPError as e:
                err = b""
                try:
                    err = e.read()
                except Exception:
                    pass
                if e.code >= 500 and attempt <= self.retries:
                    time.sleep(1.5 * attempt)
                    continue
                status, text = e.code, err.decode("utf-8", "replace")
                break
            except Exception as e:
                if attempt <= self.retries:
                    time.sleep(1.5 * attempt)
                    continue
                self.errors.append(f"{host}: {type(e).__name__}: {e}")
                raise FetchError(url, f"{type(e).__name__}: {e}") from e
        if status != 200:
            raise FetchError(url, f"HTTP {status}")
        try:
            return json.loads(text)
        except ValueError as e:
            raise FetchError(url, f"invalid JSON: {e}")

    def get_json(self, url: str, headers: dict | None = None,
                 host_delay: float | None = None):
        status, text = self.get(url, headers=headers, host_delay=host_delay)
        if status != 200:
            raise FetchError(url, f"HTTP {status}")
        try:
            return json.loads(text)
        except Exception as e:
            raise FetchError(url, f"invalid JSON: {e}") from e
