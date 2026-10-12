"""Tests for the desktop shell's platform guards and scan job.

These run on macOS, so the Windows/Linux branches are exercised by patching
`sys.platform` rather than by being on the platform. That is the point: the
whole reason these checks exist is that a user on the *other* platform finds
out at launch, and a guard that only works on the platform it guards is untested.
"""
import re
import types
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


# --- first-run failures must be VISIBLE ------------------------------------

def test_missing_config_shows_a_window_instead_of_exiting_silently(monkeypatch, tmp_path):
    """A windowed build has no console. SystemExit from load_config used to go
    nowhere, so a fresh machine looked like the app simply refused to open."""
    calls = {}

    class FakeWebview:
        @staticmethod
        def create_window(title, **kw):
            calls["title"] = title
            calls["html"] = kw.get("html", "")
            calls["url"] = kw.get("url")

        @staticmethod
        def start(*a, **k):
            calls["started"] = True

    monkeypatch.setattr(appmod.sys, "platform", "darwin")

    def boom():
        raise SystemExit("no config found at /home/x/.jobscout/config.yaml\n"
                         "run: jobscout init")

    monkeypatch.setattr(appmod, "JsApi", boom)
    monkeypatch.setitem(sys.modules, "webview", FakeWebview)

    rc = appmod.main()
    assert rc == 1
    assert calls.get("started") is True           # a window was actually shown
    assert calls["url"] is None                   # the error page, not the UI
    assert "no config found" in calls["html"]
    assert "jobscout init" in calls["html"]       # tells the user what to do
    assert "cannot start" in calls["html"]


def test_the_error_page_escapes_html(monkeypatch):
    """The message contains a path; a path can contain angle brackets."""
    calls = {}

    class FakeWebview:
        @staticmethod
        def create_window(title, **kw):
            calls["html"] = kw.get("html", "")

        @staticmethod
        def start(*a, **k):
            pass

    appmod._show_fatal(FakeWebview, "no config at <script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in calls["html"]
    assert "&lt;script&gt;" in calls["html"]


def test_happy_path_still_passes_the_api_object(monkeypatch, tmp_path):
    """The config-missing guard must not swallow the normal startup path."""
    calls = {}
    # not a bare object(): main() attaches the window list to the api object so
    # quit() can close it for the updater helper
    sentinel = types.SimpleNamespace()

    class FakeWebview:
        @staticmethod
        def create_window(title, **kw):
            calls["js_api"] = kw.get("js_api")
            calls["url"] = kw.get("url")

        @staticmethod
        def start(*a, **k):
            pass

    monkeypatch.setattr(appmod.sys, "platform", "darwin")
    monkeypatch.setattr(appmod, "JsApi", lambda: sentinel)
    monkeypatch.setitem(sys.modules, "webview", FakeWebview)

    assert appmod.main() == 0
    assert calls["js_api"] is sentinel
    assert calls["url"].endswith("index.html")


# --- the front-end's hidden-element contract -------------------------------
#
# `hidden` is honoured through a BROWSER stylesheet rule, and any author
# `display` declaration outranks it. `.pane`, `.scanbar` and `.empty` all set
# `display`, so without the [hidden] override those elements are permanently
# painted no matter what the attribute says: the Settings pane sat open on
# launch, empty (because openSettings() had never run), and its close button
# toggled an attribute that did nothing. These tests are static and fast on
# purpose -- the bug is a missing line of CSS, not a runtime condition.

WEB = appmod.WEB


def test_stylesheet_overrides_display_so_hidden_actually_hides():
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    rule = re.search(r"\[hidden\]\s*\{[^}]*display:\s*none", css)
    assert rule, (
        "styles.css must contain `[hidden] { display: none !important; }`. "
        "Without it, every element that sets `display` ignores the hidden "
        "attribute and stays on screen.")


def test_hidden_rule_comes_before_the_component_rules():
    """Order is not what makes it win (it uses !important) but keeping it at the
    top is what stops someone 'tidying' it away as a duplicate of a later
    rule."""
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    hidden_at = css.index("[hidden]")
    for cls in (".pane {", ".scanbar {", ".empty {"):
        assert hidden_at < css.index(cls), f"{cls} should come after [hidden]"


def test_every_element_that_sets_display_and_hidden_has_the_override():
    """The specific combination that broke: a class in index.html that uses the
    hidden attribute, whose stylesheet rule sets display."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert "[hidden]" in css, "the override must exist for any of this to hold"

    # every class carrying `hidden` in the markup
    hidden_classes = set(re.findall(r'class="([^"]+)"[^>]*\shidden', html))
    flat = {c for group in hidden_classes for c in group.split()}
    assert flat, "expected some elements to start hidden"

    display_classes = set(re.findall(r"\.([a-z-]+)\s*\{[^}]*display:", css))
    clash = flat & display_classes
    assert clash, "sanity: this is meant to catch the real overlap"
    # the override is global, so the clash is covered; assert it is present
    assert re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css), (
        f"classes {sorted(clash)} set display, so the global [hidden] override "
        "must be !important")


def test_panes_start_hidden_in_the_markup():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for pane in ("detail", "settings", "scrim", "scan-bar"):
        assert re.search(rf'id="{pane}"[^>]*\shidden', html), (
            f"#{pane} must start with the hidden attribute")


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


# --- the first-run restore offer -------------------------------------------
#
# Reproduces the state a user lands in after their workspace is removed: an
# empty ~/.jobscout and a backup sitting in ~/jobscout-backups. The app used to
# say only "no config found, run jobscout init" and never connect the two.

def _fake_backup(tmp_path, postings=3):
    import sqlite3 as _sq
    from jobscout import backup as _bk
    src_ws = tmp_path / "src"
    (src_ws / "reports").mkdir(parents=True)
    (src_ws / "config.yaml").write_text("# documented\npreferences:\n  min_score: 6\n")
    conn = _sq.connect(str(src_ws / "jobscout.db"))
    conn.execute("CREATE TABLE postings (id INTEGER PRIMARY KEY, title TEXT, "
                 "score REAL, filtered INTEGER DEFAULT 0, dup_of INTEGER, status TEXT)")
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT)")
    for i in range(postings):
        conn.execute("INSERT INTO postings (title, score, status) VALUES (?,?,?)",
                     (f"job {i}", 9.0, "saved" if i == 0 else "new"))
    conn.execute("INSERT INTO runs (started_at) VALUES ('2026-01-01T00:00:00')")
    conn.commit()
    conn.close()
    return _bk.backup(src_ws, tmp_path / "backups")["archive"]


def test_restore_offer_lists_backups_with_what_they_hold(tmp_path):
    arc = _fake_backup(tmp_path, postings=3)
    offer = appmod.RestoreOffer(tmp_path / "empty", [{"path": arc}])
    info = offer.info()
    assert len(info["backups"]) == 1
    b = info["backups"][0]
    assert b["name"] == arc.name
    assert b["postings"] == 3
    assert b["marked"] == 1
    assert b["created"]


def test_restore_offer_actually_restores(tmp_path):
    arc = _fake_backup(tmp_path, postings=4)
    root = tmp_path / "empty"
    offer = appmod.RestoreOffer(root, [{"path": arc}])
    res = offer.restore(str(arc))
    assert res["ok"] is True
    assert res["counts"]["postings"] == 4
    assert (root / "config.yaml").exists()
    assert offer.done is True


def test_restore_offer_reports_failure_instead_of_raising(tmp_path):
    offer = appmod.RestoreOffer(tmp_path / "empty",
                                [{"path": tmp_path / "gone.tar.gz"}])
    res = offer.restore("")
    assert res["ok"] is False
    assert "error" in res


def test_main_offers_a_restore_when_the_workspace_is_empty(tmp_path, monkeypatch):
    """The whole point: a fresh workspace plus an existing backup must produce
    an offer, not a dead end."""
    arc = _fake_backup(tmp_path, postings=2)
    created = {"windows": []}

    class FakeWebview:
        @staticmethod
        def create_window(title, **kw):
            created["windows"].append(kw)
            return object()

        @staticmethod
        def start(*a, **k):
            created["started"] = True

    monkeypatch.setattr(appmod.sys, "platform", "darwin")
    monkeypatch.setattr(appmod, "JsApi",
                        lambda: (_ for _ in ()).throw(SystemExit("no config found")))
    monkeypatch.setattr(appmod.backupmod, "default_destination",
                        lambda: tmp_path / "backups")
    monkeypatch.setenv("JOBSCOUT_HOME", str(tmp_path / "empty"))
    monkeypatch.setitem(sys.modules, "webview", FakeWebview)

    assert appmod.main() == 0
    assert created.get("started") is True
    assert created["windows"], "a window must be shown"
    assert "html" in created["windows"][0], "it is the offer page, not the UI"
    assert "js_api" in created["windows"][0], "the offer needs a bridge"


def test_main_still_shows_the_error_when_there_is_no_backup(tmp_path, monkeypatch):
    created = {}

    class FakeWebview:
        @staticmethod
        def create_window(title, **kw):
            created.update(kw)
            return object()

        @staticmethod
        def start(*a, **k):
            pass

    monkeypatch.setattr(appmod.sys, "platform", "darwin")
    monkeypatch.setattr(appmod, "JsApi",
                        lambda: (_ for _ in ()).throw(SystemExit("no config found at X")))
    monkeypatch.setattr(appmod.backupmod, "default_destination",
                        lambda: tmp_path / "no-backups-here")
    monkeypatch.setenv("JOBSCOUT_HOME", str(tmp_path / "empty"))
    monkeypatch.setitem(sys.modules, "webview", FakeWebview)

    assert appmod.main() == 1
    assert "no config found" in created.get("html", "")


# --- profiles through the window's API --------------------------------------
#
# The picker's correctness rests on these: who gets asked, that a switch really
# rebinds the workspace, and that a switch cannot strand a running scan.

@pytest.fixture()
def api(tmp_path, monkeypatch):
    """A JsApi on a throwaway workspace with one extra profile."""
    monkeypatch.setenv("JOBSCOUT_HOME", str(tmp_path / "home"))
    from jobscout import profiles
    from jobscout.config import init_workspace
    init_workspace(profiles.base_root())
    profiles.create("Second")
    return appmod.JsApi()


def test_launch_asks_when_there_is_a_choice(api):
    out = api.launch()
    assert out["ask"] is True
    assert len(out["profiles"]) == 2


def test_launch_asks_only_once_per_run(api):
    """After a switch the page reloads and asks again; it must be told no."""
    assert api.launch()["ask"] is True
    assert api.launch()["ask"] is False


def test_launch_does_not_ask_with_a_single_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBSCOUT_HOME", str(tmp_path / "home"))
    from jobscout.config import init_workspace
    from jobscout import profiles
    init_workspace(profiles.base_root())
    out = appmod.JsApi().launch()
    assert out["ask"] is False, "one profile is not a choice worth a screen"


def test_switching_rebinds_config_and_database(api):
    before = api.cfg.root
    out = api.switch_profile("Second")
    assert out["ok"] is True
    assert api.cfg.root != before
    assert api.cfg.root.name == "Second"
    # ask sqlite which file it actually has open, rather than trusting the
    # config: the point of the switch is that the CONNECTION moved too
    opened = api.conn.execute("PRAGMA database_list").fetchone()[2]
    assert opened.endswith("profiles/Second/jobscout.db"), opened


def test_switching_writes_the_choice_to_disk(api):
    from jobscout import profiles
    api.switch_profile("Second")
    assert profiles.read_active() == "Second"


def test_switching_to_something_that_is_not_there_is_refused(api):
    out = api.switch_profile("ghost")
    assert out["ok"] is False
    assert "ghost" in out["error"]


def test_a_switch_is_refused_while_a_scan_is_running(api, monkeypatch):
    """ScanJob keeps its own config and connection, so switching mid-scan would
    have it writing into the profile you just left."""
    class Busy:
        running = True

    monkeypatch.setattr(appmod, "SCAN", Busy())
    out = api.switch_profile("Second")
    assert out["ok"] is False
    assert "scan is running" in out["error"]


def test_creating_from_the_window_makes_and_activates_it(api):
    from jobscout import profiles
    out = api.create_profile("Archaeology")
    assert out["ok"] is True
    assert out["created"] == "Archaeology"
    assert profiles.read_active() == "Archaeology"
    assert api.cfg.root.name == "Archaeology", "and the window follows it"


def test_creating_reports_a_bad_name_instead_of_raising(api):
    out = api.create_profile("../escape")
    assert out["ok"] is False
    assert "letters, numbers" in out["error"]


def test_creating_without_activating_leaves_the_workspace_alone(api):
    from jobscout import profiles
    before = api.cfg.root
    out = api.create_profile("Idle", activate=False)
    assert out["ok"] is True
    assert profiles.read_active() == ""
    assert api.cfg.root == before
