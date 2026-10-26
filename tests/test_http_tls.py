"""The TLS roots every request uses.

The failure this pins: the packaged app built its own Fetcher without a TLS
context. A PyInstaller bundle carries its own OpenSSL with no certificate store,
so every HTTPS request died with CERTIFICATE_VERIFY_FAILED — and the scan
summary reported the result as "0 new", which reads like a quiet week rather
than a broken app. The CLI passed a certifi context and worked, which is what
made the difference invisible in testing.
"""
from pathlib import Path

import certifi

from jobscout.http import Fetcher, default_ssl_context


def test_the_default_context_is_certifis_bundle():
    """Count the certificates, not just "it is not None": an empty store on a
    machine that happens to have system roots would still pass the weak check."""
    ctx = default_ssl_context()
    assert ctx is not None, "certifi is a declared dependency; this should never be None"
    pem = Path(certifi.where()).read_text(encoding="utf-8")
    assert len(ctx.get_ca_certs()) == pem.count("BEGIN CERTIFICATE")


def test_a_fetcher_gets_that_context_without_being_asked():
    """Every caller must inherit the roots; forgetting them is the original bug."""
    f = Fetcher(host_delay=0)
    assert f.ssl_context is not None
    assert len(f.ssl_context.get_ca_certs()) > 100


def test_an_explicit_context_still_wins():
    """EURES' own context (or a test's) must not be overwritten by the default."""
    import ssl

    mine = ssl.create_default_context()
    assert Fetcher(host_delay=0, ssl_context=mine).ssl_context is mine


def test_the_context_is_built_once():
    """A context per Fetcher would parse the bundle on every construction."""
    assert default_ssl_context() is default_ssl_context()
