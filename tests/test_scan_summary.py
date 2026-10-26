"""What a scan summary must contain for the window to be honest.

A run where every board failed reported `0 new` and nothing else, so the window
said "0 new · 0 matches this run" — indistinguishable from a quiet day. The
TLS bug behind that cost five days of scans before anyone could see it.
"""
import types

from jobscout import db as dbm
from jobscout import scanner
from jobscout.http import Fetcher, FetchError
from jobscout.models import Posting


def _cfg(tmp_path, **prefs):
    base = {"min_score": 6, "scan_workers": 1, "title_terms": []}
    base.update(prefs)
    return types.SimpleNamespace(prefs=base, enabled=set(), root=tmp_path)


def _source(name, postings=None, error=None):
    def fetch(settings, fetcher):
        if error:
            raise error
        return postings or []
    return types.SimpleNamespace(fetch=fetch)


def test_the_summary_counts_what_was_read(tmp_path, monkeypatch):
    conn = dbm.connect(tmp_path / "db.sqlite")
    postings = [Posting(source="a", title="Backend Engineer", company="ACME",
                        location="Berlin", url="https://a/1", external_id="1"),
                Posting(source="a", title="Platform Engineer", company="ACME",
                        location="Berlin", url="https://a/2", external_id="2")]
    monkeypatch.setattr(scanner, "iter_sources", lambda enabled: [("a", _source("a", postings))])
    monkeypatch.setattr(scanner, "source_settings", lambda cfg, name: {})

    out = scanner.scan(_cfg(tmp_path), conn, Fetcher(host_delay=0))

    assert out["fetched"] == 2, "the window needs this to tell silence from failure"
    assert out["errors"] == []


def test_a_scan_that_could_not_read_anything_says_so(tmp_path, monkeypatch):
    conn = dbm.connect(tmp_path / "db.sqlite")
    boom = FetchError("https://a/1", "URLError: certificate verify failed")
    monkeypatch.setattr(scanner, "iter_sources",
                        lambda enabled: [("a", _source("a", error=boom)),
                                         ("b", _source("b", error=boom))])
    monkeypatch.setattr(scanner, "source_settings", lambda cfg, name: {})

    out = scanner.scan(_cfg(tmp_path), conn, Fetcher(host_delay=0))

    assert out["fetched"] == 0
    assert len(out["errors"]) == 2, "one error per source, both kept for the window"
    assert "certificate verify failed" in out["errors"][0]
