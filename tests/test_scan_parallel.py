"""Sources are separate hosts, so the scan fetches them at the same time.

The fetcher keeps its per-host delay; what changes is that waiting for board A
no longer blocks board B. Without the pool this test deadlocks on the barrier and
the failure shows up as two source errors.
"""
import threading
import types

from jobscout import db as dbm
from jobscout import scanner
from jobscout.http import Fetcher


def test_sources_scan_in_parallel_when_configured(tmp_path, monkeypatch):
    conn = dbm.connect(tmp_path / "db.sqlite")
    cfg = types.SimpleNamespace(
        prefs={"min_score": 6, "scan_workers": 2, "title_terms": []},
        enabled=set(), root=tmp_path)
    barrier = threading.Barrier(2, timeout=2.0)

    def make(name):
        def fetch(settings, fetcher):
            barrier.wait()          # both sources must be in flight at once
            return []
        return types.SimpleNamespace(fetch=fetch)

    monkeypatch.setattr(scanner, "iter_sources",
                        lambda enabled: [("a", make("a")), ("b", make("b"))])
    monkeypatch.setattr(scanner, "source_settings", lambda cfg, name: {})

    out = scanner.scan(cfg, conn, Fetcher())
    assert out["errors"] == [], "with one worker the barrier times out"
    assert sorted(out["sources"]) == ["a", "b"]


def test_one_worker_keeps_the_old_order(tmp_path, monkeypatch):
    conn = dbm.connect(tmp_path / "db.sqlite")
    cfg = types.SimpleNamespace(
        prefs={"min_score": 6, "scan_workers": 1, "title_terms": []},
        enabled=set(), root=tmp_path)
    seen = []

    def make(name):
        def fetch(settings, fetcher):
            seen.append(name)
            return []
        return types.SimpleNamespace(fetch=fetch)

    monkeypatch.setattr(scanner, "iter_sources",
                        lambda enabled: [("a", make("a")), ("b", make("b"))])
    monkeypatch.setattr(scanner, "source_settings", lambda cfg, name: {})

    out = scanner.scan(cfg, conn, Fetcher())
    assert out["errors"] == []
    assert seen == ["a", "b"], "one worker must not reorder the sources"
