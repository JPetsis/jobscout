"""Tests for the desktop shell's platform guards and scan job.

These run on macOS, so the Windows/Linux branches are exercised by patching
`sys.platform` rather than by being on the platform. That is the point: the
whole reason these checks exist is that a user on the *other* platform finds
out at launch, and a guard that only works on the platform it guards is untested.
"""
import sys
import threading
import time

import pytest

from jobscout import app as appmod


# --- platform guards -------------------------------------------------------

def test_webview2_check_is_a_noop_off_windows(monkeypatch):
    """On macOS/Linux there is no winreg, and the check must not fire."""
    monkeypatch.setattr(appmod.sys, "platform", "darwin")
    appmod._check_backend()                        # must not raise
    assert appmod._has_webview2() is True          # no winreg -> assume present


def test_windows_without_webview2_fails_with_a_fixable_message(monkeypatch):
    monkeypatch.setattr(appmod.sys, "platform", "win32")
    monkeypatch.setattr(appmod, "_has_webview2", lambda: False)
    with pytest.raises(SystemExit) as e:
        appmod._check_backend()
    msg = str(e.value)
    assert "WebView2" in msg
    assert appmod.WEBVIEW2_URL in msg              # the user is told where to go
    assert "Evergreen" in msg


def test_windows_with_webview2_passes(monkeypatch):
    monkeypatch.setattr(appmod.sys, "platform", "win32")
    monkeypatch.setattr(appmod, "_has_webview2", lambda: True)
    appmod._check_backend()                        # must not raise


def test_linux_without_a_backend_fails_with_the_pip_extra(monkeypatch):
    monkeypatch.setattr(appmod.sys, "platform", "linux")
    real_import = __import__

    def fake_import(name, *a, **k):
        if name in ("qtpy", "gi"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(SystemExit) as e:
        appmod._check_backend()
    assert "pywebview[qt]" in str(e.value)


def test_linux_with_qt_passes(monkeypatch):
    """A backend that imports cleanly must satisfy the check."""
    import types

    monkeypatch.setattr(appmod.sys, "platform", "linux")
    fake = types.ModuleType("qtpy")
    monkeypatch.setitem(sys.modules, "qtpy", fake)
    appmod._check_backend()                        # must not raise


# --- the scan job ----------------------------------------------------------

def test_scan_job_reports_a_start_failure_instead_of_raising(monkeypatch, tmp_path):
    """Two clicks on Scan must not start two scans, and must not explode."""
    cfg = type("C", (), {"root": tmp_path, "enabled": [], "prefs": {}})()

    job = appmod.ScanJob(cfg)
    monkeypatch.setattr(job, "_run", lambda only: time.sleep(0.4))
    assert job.start()["started"] is True
    second = job.start()
    assert second["started"] is False
    assert "already running" in second["reason"]
    job._thread.join(timeout=3)


def test_scan_job_passes_only_through_as_a_tuple(monkeypatch, tmp_path):
    """The bug this guards: args=(only or set()) is a set, not a tuple, so the
    worker thread died instantly with 'missing 1 required positional argument'."""
    cfg = type("C", (), {"root": tmp_path, "enabled": [], "prefs": {}})()
    job = appmod.ScanJob(cfg)
    seen = []

    def fake_run(only):
        seen.append(only)

    monkeypatch.setattr(job, "_run", fake_run)
    job.start({"nodesk", "wwr"})
    job._thread.join(timeout=3)
    # the real _run clears `running` in its finally; this fake does not, so the
    # only meaningful assertion is that the set arrived as ONE argument
    assert seen == [{"nodesk", "wwr"}]


def test_scan_job_defaults_to_an_empty_set(monkeypatch, tmp_path):
    cfg = type("C", (), {"root": tmp_path, "enabled": [], "prefs": {}})()
    job = appmod.ScanJob(cfg)
    seen = []
    monkeypatch.setattr(job, "_run", lambda only: seen.append(only))
    job.start()
    job._thread.join(timeout=3)
    assert seen == [set()]


def test_scan_job_drains_events_once(tmp_path):
    """Draining (not replaying) is what keeps the payload bounded."""
    cfg = type("C", (), {"root": tmp_path, "enabled": [], "prefs": {}})()
    job = appmod.ScanJob(cfg)
    job.events.extend([{"kind": "a"}, {"kind": "b"}])
    first = job.drain()
    assert [e["kind"] for e in first["events"]] == ["a", "b"]
    assert job.drain()["events"] == []              # consumed, not replayed


def test_scan_job_event_buffer_is_capped(tmp_path):
    cfg = type("C", (), {"root": tmp_path, "enabled": [], "prefs": {}})()
    job = appmod.ScanJob(cfg)
    for i in range(6000):
        job._emit({"kind": "x", "i": i})
    assert len(job.events) == 5000


def test_scan_job_cancel_when_idle_is_not_an_error(tmp_path):
    cfg = type("C", (), {"root": tmp_path, "enabled": [], "prefs": {}})()
    job = appmod.ScanJob(cfg)
    res = job.cancel()
    assert res["cancelled"] is False
    assert "nothing running" in res["reason"]


def test_scan_job_records_an_error_instead_of_dying_silently(monkeypatch, tmp_path):
    cfg = type("C", (), {"root": tmp_path, "enabled": [], "prefs": {}})()
    job = appmod.ScanJob(cfg)

    def boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(appmod.scanner, "scan", boom)
    monkeypatch.setattr(appmod.dbm, "connect", lambda *a, **k: _FakeConn())
    job.start()
    for _ in range(50):
        time.sleep(0.02)
        if not job.running:
            break
    assert job.error and "no network" in job.error
    assert job.running is False
    assert job.drain()["error"] == job.error


class _FakeConn:
    def execute(self, *a, **k):
        return self

    def close(self):
        pass


# --- threading sanity ------------------------------------------------------

def test_two_connections_to_one_file_are_fine(tmp_path):
    """The scan uses its own connection on purpose; prove that is safe."""
    import sqlite3

    db = tmp_path / "t.db"
    c1 = sqlite3.connect(str(db), check_same_thread=False)
    c2 = sqlite3.connect(str(db), check_same_thread=False)
    c1.execute("CREATE TABLE t (x INTEGER)")
    c1.commit()
    errors = []

    def writer():
        try:
            for i in range(50):
                c2.execute("INSERT INTO t VALUES (?)", (i,))
                c2.commit()
        except Exception as e:                                    # noqa: BLE001
            errors.append(e)

    def reader():
        try:
            for _ in range(50):
                c1.execute("SELECT COUNT(*) FROM t").fetchone()
        except Exception as e:                                    # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert not errors, errors
