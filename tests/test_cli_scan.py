"""The scan view must PRINT actionable source errors, not just count them.

Reported problem this guards: a keyed source enabled without a key fetched
nothing and produced one line saying "N source error(s)", with the actual
explanation buried in the digest footer. That is indistinguishable from a
source that simply had no jobs today, so the user never learns they need a key.
"""
import types
from pathlib import Path

import pytest

from jobscout import cli, scanner


def _summary(**over):
    base = {
        "run_id": 1, "new": 0, "matches": 0, "total_matches": 0, "strong": 0,
        "min_score": 6, "strong_threshold": 10, "matches_top": [],
        "errors": [], "requests": 0, "sources": {}, "cancelled": False,
        "unmarked": 0,
    }
    base.update(over)
    return base


@pytest.fixture()
def scan_env(tmp_path, monkeypatch):
    """cmd_scan against a scratch workspace with the scan loop stubbed out."""
    cfg = types.SimpleNamespace(root=tmp_path, prefs={"min_score": 6,
                                                      "strong_threshold": 10},
                                enabled=[], path=tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text("preferences:\n  min_score: 6\n", encoding="utf-8")
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "_verify_on_scan", lambda *a, **k: None)
    return cfg


def _run(monkeypatch, summary):
    monkeypatch.setattr(scanner, "scan", lambda *a, **k: summary)
    args = types.SimpleNamespace(only="", quiet=False)
    return cli.cmd_scan(args)


def test_source_errors_are_printed_with_their_message(capsys, scan_env, monkeypatch):
    msg = ("adzuna: missing app_id/app_key — set sources.adzuna.app_id and "
           "sources.adzuna.app_key in the config; free instant key: "
           "https://developer.adzuna.com/signup")
    _run(monkeypatch, _summary(errors=[msg]))
    out = capsys.readouterr().out
    assert "1 source error(s)" in out
    assert "sources.adzuna.app_id" in out          # the actionable part
    assert "developer.adzuna.com/signup" in out    # and the URL, uncut


def test_the_error_message_is_not_truncated_before_its_url(capsys, scan_env, monkeypatch):
    """The URL sits at the END of these messages, so a head-only cut removes
    exactly the half that tells the user what to do."""
    msg = "usajobs: " + ("x" * 200) + " https://developer.usajobs.gov/apirequest/"
    _run(monkeypatch, _summary(errors=[msg]))
    out = capsys.readouterr().out
    assert "developer.usajobs.gov/apirequest/" in out


def test_a_clean_scan_prints_no_error_lines(capsys, scan_env, monkeypatch):
    _run(monkeypatch, _summary())
    out = capsys.readouterr().out
    assert "error" not in out.lower()


def test_many_errors_are_bounded(capsys, scan_env, monkeypatch):
    errors = [f"src{i}: something went wrong" for i in range(11)]
    _run(monkeypatch, _summary(errors=errors))
    out = capsys.readouterr().out
    assert "11 source error(s)" in out
    assert "and 5 more" in out                     # 6 shown, rest summarised
    assert "src0: something" in out
    assert "src10: something" not in out


def test_cancelled_scan_says_so(capsys, scan_env, monkeypatch):
    _run(monkeypatch, _summary(cancelled=True))
    assert "cancelled" in capsys.readouterr().out


# --- `--only X` where X is not enabled -------------------------------------

def test_scanning_a_source_that_is_not_enabled_says_so(capsys, scan_env, monkeypatch):
    """Without this, `--only themuse` scans nothing and prints "0 sources",
    which reads as "that board had no jobs" rather than "you never enabled it".
    Hit for real while verifying the new US sources."""
    def fake_scan(cfg, conn, fetcher, *, only=None, on_event=None, **_):
        if on_event:
            on_event({"kind": "not_enabled", "names": sorted(only or []),
                      "enabled_count": 0})
        return _summary()

    monkeypatch.setattr(scanner, "scan", fake_scan)
    cli.cmd_scan(types.SimpleNamespace(only="themuse", quiet=False))
    out = capsys.readouterr().out
    assert "themuse is not enabled" in out
    assert "sources.enabled" in out            # says how to fix it
    assert "Settings" in out


# --- `jobscout update` ------------------------------------------------------
#
# The wording matters here: "you are current" and "nothing has been published"
# are different states, and the second one is what a draft-only repo reports.

def _update_env(monkeypatch, tmp_path, info):
    import types as _t
    from jobscout import update as updatemod
    cfg = _t.SimpleNamespace(root=tmp_path, prefs={}, enabled=[],
                             path=tmp_path / "config.yaml")
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(updatemod, "check", lambda *a, **k: info)


def test_update_says_current_when_a_release_is_newer_absent(capsys, monkeypatch, tmp_path):
    _update_env(monkeypatch, tmp_path, {
        "ok": True, "current": "0.1.0", "latest": "0.1.0", "update_available": False})
    cli.cmd_update(types.SimpleNamespace(check=True, yes=False, from_archive=None,
                                         stage_only=False, no_relaunch=False,
                                         prerelease=False))
    out = capsys.readouterr().out
    assert "is the latest release" in out
    assert "0.1.0" in out


def test_update_distinguishes_no_published_release_from_being_current(capsys, monkeypatch, tmp_path):
    """A draft release is not downloadable by users, so `latest` is None and
    claiming "you are up to date" would be a guess."""
    _update_env(monkeypatch, tmp_path, {
        "ok": True, "current": "0.1.0", "latest": None, "update_available": False})
    cli.cmd_update(types.SimpleNamespace(check=True, yes=False, from_archive=None,
                                         stage_only=False, no_relaunch=False,
                                         prerelease=False))
    out = capsys.readouterr().out
    assert "no releases published yet" in out
    assert "latest release" not in out


def test_update__check_does_not_download(capsys, monkeypatch, tmp_path):
    _update_env(monkeypatch, tmp_path, {
        "ok": True, "current": "0.1.0", "latest": "0.2.0",
        "update_available": True, "asset": {"name": "jobscout-macos-arm64.zip",
                                            "size": 100 * 1048576, "url": "u"},
        "url": "https://example.invalid/rel", "asset_for_platform": "x"})
    cli.cmd_update(types.SimpleNamespace(check=True, yes=False, from_archive=None,
                                         stage_only=False, no_relaunch=False,
                                         prerelease=False))
    out = capsys.readouterr().out
    assert "update available" in out
    assert "0.2.0" in out and "100 MB" in out
    assert "nothing downloaded" in out


def test_update_without_yes_explains_and_does_nothing(capsys, monkeypatch, tmp_path):
    from jobscout import update as updatemod
    _update_env(monkeypatch, tmp_path, {
        "ok": True, "current": "0.1.0", "latest": "0.2.0",
        "update_available": True, "asset": {"name": "a.zip", "size": 10, "url": "u"},
        "url": "https://example.invalid/rel", "asset_for_platform": "x"})
    monkeypatch.setattr(updatemod, "install_root", lambda: tmp_path / "app")
    called = []
    monkeypatch.setattr(updatemod, "update",
                        lambda *a, **k: called.append(1) or {})
    cli.cmd_update(types.SimpleNamespace(check=False, yes=False, from_archive=None,
                                         stage_only=False, no_relaunch=False,
                                         prerelease=False))
    out = capsys.readouterr().out
    assert "--yes" in out
    assert called == [], "must not install without --yes"


def test_update_reports_a_source_checkout_instead_of_pretending(capsys, monkeypatch, tmp_path):
    from jobscout import update as updatemod
    _update_env(monkeypatch, tmp_path, {
        "ok": True, "current": "0.1.0", "latest": "0.2.0",
        "update_available": True, "asset": {"name": "a.zip", "size": 10, "url": "u"},
        "url": "https://example.invalid/rel", "asset_for_platform": "x"})
    monkeypatch.setattr(updatemod, "install_root", lambda: None)
    cli.cmd_update(types.SimpleNamespace(check=False, yes=True, from_archive=None,
                                         stage_only=False, no_relaunch=False,
                                         prerelease=False))
    out = capsys.readouterr().out
    assert "source checkout" in out
    assert "git pull" in out


# --- `jobscout restore` -----------------------------------------------------

def _backup_env(monkeypatch, tmp_path, *, has_workspace=True):
    import types as _t
    import sqlite3
    from jobscout import backup as bk
    from jobscout.config import DEFAULT_CONFIG

    src_ws = tmp_path / "source"
    (src_ws / "reports").mkdir(parents=True)
    (src_ws / "config.yaml").write_text(DEFAULT_CONFIG)
    conn = sqlite3.connect(str(src_ws / "jobscout.db"))
    conn.execute("CREATE TABLE postings (id INTEGER PRIMARY KEY, title TEXT, "
                 "score REAL, filtered INTEGER DEFAULT 0, dup_of INTEGER, status TEXT)")
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT)")
    conn.executemany("INSERT INTO postings (title, score, status) VALUES (?,?,?)",
                     [("a", 1.0, "saved"), ("b", 1.0, "new")])
    conn.execute("INSERT INTO runs (started_at) VALUES ('2026-01-01T00:00:00')")
    conn.commit()
    conn.close()
    arc = bk.backup(src_ws, tmp_path / "backups")["archive"]

    root = tmp_path / "ws"
    if has_workspace:
        root.mkdir(parents=True)
        (root / "config.yaml").write_text("# live\npreferences:\n  min_score: 7\n")
    cfg = _t.SimpleNamespace(root=root, prefs={}, enabled=[],
                             path=root / "config.yaml")
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    return arc, root


def _restore_args(tmp_path, **over):
    import types as _t
    base = dict(from_archive=None, to=str(tmp_path / "backups"),
                overwrite=False, yes=False)
    base.update(over)
    return _t.SimpleNamespace(**base)


def test_restore_refuses_to_clobber_a_live_workspace(capsys, monkeypatch, tmp_path):
    _backup_env(monkeypatch, tmp_path)
    rc = cli.cmd_restore(_restore_args(tmp_path))
    out = capsys.readouterr().out
    assert rc == 1
    assert "already has a config or database" in out
    assert "--overwrite" in out
    # and it says the current state will be saved
    assert "copied to a new backup" in out


def test_restore_shows_what_is_in_the_backup_before_touching_anything(capsys, monkeypatch, tmp_path):
    _backup_env(monkeypatch, tmp_path)
    cli.cmd_restore(_restore_args(tmp_path))
    out = capsys.readouterr().out
    assert "2 postings" in out          # so the user can judge
    assert "1 marked" in out


def test_restore_into_an_empty_workspace_succeeds(capsys, monkeypatch, tmp_path):
    arc, root = _backup_env(monkeypatch, tmp_path, has_workspace=False)
    rc = cli.cmd_restore(_restore_args(tmp_path))
    out = capsys.readouterr().out
    assert rc == 0
    assert "restored" in out
    assert (root / "jobscout.db").exists()
    assert (root / "config.yaml").exists()


def test_restore_with_nothing_available_says_so(capsys, monkeypatch, tmp_path):
    import types as _t
    cfg = _t.SimpleNamespace(root=tmp_path / "ws", prefs={}, enabled=[],
                             path=tmp_path / "ws" / "config.yaml")
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    rc = cli.cmd_restore(_restore_args(tmp_path))
    out = capsys.readouterr().out
    assert rc == 1
    assert "no backups found" in out
    assert "jobscout backup" in out       # says how to make one


def test_scan_top_matches_render_the_contract_pill(capsys, scan_env, monkeypatch):
    """The contract signal must be visible where matches are listed, not only
    inside the why-breakdown: the Leipzig unicorn was found, but 'unbefristet'
    was invisible in the scan summary until this pill existed."""
    _run(monkeypatch, _summary(matches_top=[(7, 13.0, "Full Stack Entwickler",
                                             "Uni Leipzig", "Leipzig", "perm:unbefristet"),
                                            (9, 9.0, "Moodle Developer",
                                             "Uni Tübingen", "Tübingen", "tmp:befristet")]))
    out = capsys.readouterr().out
    assert "[unbefristet]" in out
    assert "[befristet]" in out
