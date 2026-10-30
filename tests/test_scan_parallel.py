"""Sources are separate hosts, so the scan fetches them at the same time.

The fetcher keeps its per-host delay; what changes is that waiting for board A
no longer blocks board B. Without the pool this test deadlocks on the barrier and
the failure shows up as two source errors.
"""
import threading
import time
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


def test_progress_counts_finished_sources_not_their_position(tmp_path, monkeypatch):
    """The bar walked backwards because it was drawn from `index/of`, and with
    the pool the board at index 6 answers before the one at index 2: 6/40 then
    3/40 reads as a rewind. A count of finished sources only ever goes up."""
    conn = dbm.connect(tmp_path / "db.sqlite")
    cfg = types.SimpleNamespace(
        prefs={"min_score": 6, "scan_workers": 2, "title_terms": []},
        enabled=set(), root=tmp_path)

    def make(name):
        def fetch(settings, fetcher):
            if name == "a":
                time.sleep(0.15)      # first in the list, last to answer
            return []
        return types.SimpleNamespace(fetch=fetch)

    monkeypatch.setattr(scanner, "iter_sources",
                        lambda enabled: [("a", make("a")), ("b", make("b"))])
    monkeypatch.setattr(scanner, "source_settings", lambda cfg, name: {})

    events = []
    scanner.scan(cfg, conn, Fetcher(), on_event=events.append)
    fetched = [e for e in events if e["kind"] == "source_fetched"]
    assert [e["done"] for e in fetched] == [1, 2], "finish order, not list order"
    assert [e["name"] for e in fetched] == ["b", "a"]
    assert all(e["of"] == 2 for e in fetched)


def test_a_source_scanned_inside_the_window_is_not_fetched_again(tmp_path, monkeypatch):
    """47 sources took 26 minutes, and pressing Scan again 40 minutes later did
    all of it again: the freshness setting was only honoured at boot."""
    conn = dbm.connect(tmp_path / "db.sqlite")
    cfg = types.SimpleNamespace(
        prefs={"min_score": 6, "scan_workers": 1, "title_terms": [],
               "scan_if_older_hours": 24},
        enabled=set(), root=tmp_path)
    calls = []

    def make(name):
        def fetch(settings, fetcher):
            calls.append(name)
            return []
        return types.SimpleNamespace(fetch=fetch)

    monkeypatch.setattr(scanner, "iter_sources",
                        lambda enabled: [("a", make("a")), ("b", make("b"))])
    monkeypatch.setattr(scanner, "source_settings", lambda cfg, name: {})

    scanner.scan(cfg, conn, Fetcher())
    assert calls == ["a", "b"]

    calls.clear()
    out = scanner.scan(cfg, conn, Fetcher())
    assert calls == [], "nothing is due inside the window"
    assert sorted(out["skipped_fresh"]) == ["a", "b"]

    out = scanner.scan(cfg, conn, Fetcher(), force=True)
    assert calls == ["a", "b"], "force overrides the window"


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
