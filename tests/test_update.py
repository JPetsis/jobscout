"""Tests for the update mechanism.

The properties that matter, in order of how much damage their absence causes:
  1. a failed swap must RESTORE the working build, never leave nothing that
     launches;
  2. a download that does not match its published checksum must be refused;
  3. an archive that is not a jobscout build must be refused before it is
     swapped in;
  4. a prerelease must never be offered over its own release (that was a real
     bug: the naive tuple made 0.2.0-rc1 look newer than 0.2.0).
"""
import hashlib
import json
import os
import subprocess
import tarfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobscout import update as up

DEAD_PID = 999999        # not running, so the helper's wait passes at once


# --- version comparison ----------------------------------------------------

@pytest.mark.parametrize("candidate,current,expected", [
    ("v0.2.0", "0.1.0", True),
    ("0.2.0", "v0.1.0", True),
    ("v0.1.0", "0.1.0", False),
    ("v0.9.0", "v0.10.0", False),      # numeric, not lexical
    ("v0.10.0", "v0.9.0", True),
    ("v1.0.0", "0.99.99", True),
    ("v0.1.1", "0.1.0", True),
    ("v0.2.0", "0.2.0-rc1", True),     # a release beats its own rc
    ("v0.2.0-rc1", "0.2.0", False),    # and an rc never beats the release
    ("v0.2.0-rc2", "0.2.0-rc1", True),
    ("garbage", "0.1.0", False),
    ("", "0.1.0", False),
])
def test_is_newer(candidate, current, expected):
    assert up.is_newer(candidate, current) is expected


def test_a_prerelease_is_never_offered_over_its_release():
    """The bug this guards: (0,2,0) compares as a PREFIX of (0,2,0,0,'rc1')
    and therefore as smaller, so the app would have offered a downgrade."""
    assert not up.is_newer("v0.2.0-rc1", "0.2.0")
    assert up.parse_version("0.2.0") > up.parse_version("0.2.0-rc1")


# --- platform asset naming -------------------------------------------------

def test_platform_asset_matches_what_the_workflow_publishes(monkeypatch):
    """These names have to agree with .github/workflows/build.yml or the update
    downloads a build for the wrong architecture."""
    published = {
        "jobscout-macos-arm64.zip", "jobscout-macos-x86_64.zip",
        "jobscout-linux-arm64.tar.gz", "jobscout-linux-x86_64.tar.gz",
        "jobscout-windows-x86_64.zip",
    }
    for plat, machine, expected in [
        ("darwin", "arm64", "jobscout-macos-arm64.zip"),
        ("darwin", "x86_64", "jobscout-macos-x86_64.zip"),
        ("linux", "aarch64", "jobscout-linux-arm64.tar.gz"),
        ("linux", "x86_64", "jobscout-linux-x86_64.tar.gz"),
        ("win32", "AMD64", "jobscout-windows-x86_64.zip"),
    ]:
        monkeypatch.setattr(up.sys, "platform", plat)
        monkeypatch.setattr(up.platform, "machine", lambda m=machine: m)
        got = up.platform_asset()
        assert got == expected
        assert got in published


def test_unknown_platform_is_reported_not_guessed(monkeypatch):
    monkeypatch.setattr(up.platform, "machine", lambda: "riscv64")
    with pytest.raises(up.UpdateError, match="no prebuilt jobscout"):
        up.platform_asset()


# --- the check cache -------------------------------------------------------

def test_a_disabled_check_makes_no_request(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(up, "latest_release", lambda **k: calls.append(1) or None)
    info = up.check(tmp_path, force=True)
    assert info["ok"] is True and calls == [1]


def test_the_check_is_cached_for_a_day(tmp_path, monkeypatch):
    calls = []

    class FakeRelease(up.Release):
        pass

    def fake_latest(**k):
        calls.append(1)
        return up.Release(tag="v0.1.0", version="0.1.0", assets={})

    monkeypatch.setattr(up, "latest_release", fake_latest)
    up.check(tmp_path, force=True)
    assert len(calls) == 1
    again = up.check(tmp_path)                  # within the window
    assert len(calls) == 1, "the second call must come from the cache"
    assert again["from_cache"] is True


def test_a_stale_cache_is_refreshed(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(up, "latest_release",
                        lambda **k: calls.append(1) or None)
    up.check(tmp_path, force=True)
    # age the cache past the interval
    path = tmp_path / "update-check.json"
    data = json.loads(path.read_text())
    data["checked_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    path.write_text(json.dumps(data))
    up.check(tmp_path)
    assert len(calls) == 2


def test_an_unwritable_cache_does_not_break_the_check(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "latest_release", lambda **k: None)
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        info = up.check(ro, force=True)
        assert info["ok"] is True
    finally:
        ro.chmod(0o700)


def test_a_network_failure_is_reported_not_raised(tmp_path, monkeypatch):
    def boom(**k):
        raise up.UpdateError("GitHub answered HTTP 503")

    monkeypatch.setattr(up, "latest_release", boom)
    info = up.check(tmp_path, force=True)
    assert info["ok"] is False
    assert "503" in info["error"]


def test_drafts_are_ignored(monkeypatch):
    """A draft is not downloadable by anyone but the owner, so offering it
    would advertise a build the user cannot fetch."""
    payload = [
        {"tag_name": "v0.9.0", "draft": True, "prerelease": False, "assets": []},
        {"tag_name": "v0.2.0", "draft": False, "prerelease": False, "assets": [
            {"name": "jobscout-macos-arm64.zip", "browser_download_url": "u",
             "size": 1, "digest": "sha256:ab"}]},
    ]
    monkeypatch.setattr(up, "_get_json", lambda url: payload)
    rel = up.latest_release()
    assert rel.tag == "v0.2.0"


def test_prereleases_are_excluded_unless_asked_for(monkeypatch):
    payload = [
        {"tag_name": "v0.3.0-rc1", "draft": False, "prerelease": True, "assets": []},
        {"tag_name": "v0.2.0", "draft": False, "prerelease": False, "assets": []},
    ]
    monkeypatch.setattr(up, "_get_json", lambda url: payload)
    assert up.latest_release().tag == "v0.2.0"
    assert up.latest_release(include_prerelease=True).tag == "v0.3.0-rc1"


def test_no_releases_at_all_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(up, "_get_json", lambda url: [])
    info = up.check(tmp_path, force=True)
    assert info["ok"] is True
    assert info["latest"] is None
    assert info["update_available"] is False


def test_asset_digest_is_parsed(monkeypatch):
    payload = [{"tag_name": "v0.2.0", "draft": False, "prerelease": False, "assets": [
        {"name": "jobscout-macos-arm64.zip", "browser_download_url": "https://x/y.zip",
         "size": 42, "digest": "sha256:deadbeef"}]}]
    monkeypatch.setattr(up, "_get_json", lambda url: payload)
    a = up.latest_release().assets["jobscout-macos-arm64.zip"]
    assert a.sha256 == "deadbeef" and a.size == 42 and a.url == "https://x/y.zip"


# --- download verification -------------------------------------------------

def _serve(tmp_path, content: bytes) -> Path:
    src = tmp_path / "src.bin"
    src.write_bytes(content)
    return src


def test_a_matching_checksum_is_accepted(tmp_path):
    src = _serve(tmp_path, b"payload")
    want = hashlib.sha256(b"payload").hexdigest()
    dest = up.download(f"file://{src}", tmp_path / "out.bin", expected_sha256=want)
    assert dest.read_bytes() == b"payload"


def test_a_wrong_checksum_is_refused_and_nothing_is_left(tmp_path):
    src = _serve(tmp_path, b"payload")
    with pytest.raises(up.UpdateError, match="checksum"):
        up.download(f"file://{src}", tmp_path / "out.bin", expected_sha256="0" * 64)
    assert not (tmp_path / "out.bin").exists()
    assert not list(tmp_path.glob("*.part"))


def test_a_wrong_size_is_refused(tmp_path):
    src = _serve(tmp_path, b"payload")
    with pytest.raises(up.UpdateError, match="bytes"):
        up.download(f"file://{src}", tmp_path / "out.bin", expected_size=999)


def test_a_failed_download_leaves_no_partial_file(tmp_path):
    with pytest.raises(up.UpdateError, match="download failed"):
        up.download(f"file://{tmp_path/'nope.bin'}", tmp_path / "out.bin")
    assert not list(tmp_path.glob("*.part"))
    assert not (tmp_path / "out.bin").exists()


# --- extract ---------------------------------------------------------------

def test_extract_finds_a_macos_bundle(tmp_path):
    arc = tmp_path / "jobscout-macos-arm64.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("jobscout.app/Contents/MacOS/jobscout", "binary")
    payload = up.extract(arc, tmp_path / "out")
    assert payload.name == "jobscout.app"


def test_extract_finds_a_onedir_build(tmp_path):
    arc = tmp_path / "jobscout-linux-x86_64.tar.gz"
    inner = tmp_path / "jobscout" / "_internal"
    inner.mkdir(parents=True)
    (tmp_path / "jobscout" / "jobscout").write_text("binary")
    with tarfile.open(arc, "w:gz") as t:
        t.add(tmp_path / "jobscout", arcname="jobscout")
    payload = up.extract(arc, tmp_path / "out")
    assert payload.name == "jobscout"


def test_extract_refuses_a_non_jobscout_archive(tmp_path):
    arc = tmp_path / "random.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("photos/holiday.jpg", "not a build")
        z.writestr("notes.txt", "hello")
    with pytest.raises(up.UpdateError, match="does not look like a jobscout build"):
        up.extract(arc, tmp_path / "out")


def test_extract_refuses_garbage(tmp_path):
    arc = tmp_path / "jobscout-9.9.9.zip"
    arc.write_bytes(b"not a zip at all")
    with pytest.raises(up.UpdateError, match="could not unpack"):
        up.extract(arc, tmp_path / "out")


def test_extract_refuses_an_unsupported_extension(tmp_path):
    arc = tmp_path / "jobscout.rar"
    arc.write_bytes(b"x")
    with pytest.raises(up.UpdateError, match="unsupported archive"):
        up.extract(arc, tmp_path / "out")


# --- the swap, and its rollback -------------------------------------------

def _build(root: Path, marker: str) -> Path:
    root.mkdir(parents=True)
    (root / "marker.txt").write_text(marker)
    return root


def _run_helper(body: str, path: Path):
    path.write_text(body)
    path.chmod(0o755)
    return subprocess.run([str(path)], capture_output=True, text=True, timeout=30)


def test_the_helper_swaps_the_build(tmp_path):
    old = _build(tmp_path / "app", "OLD")
    new = _build(tmp_path / "staged" / "app", "NEW")
    r = _run_helper(up._helper_script(old, new, relaunch=False, pid=DEAD_PID),
                    tmp_path / "apply.sh")
    assert r.returncode == 0, r.stderr
    assert (old / "marker.txt").read_text() == "NEW"
    assert not new.exists()
    assert not Path(str(old) + ".previous").exists()


def test_a_failed_swap_restores_the_working_build(tmp_path):
    """The property that matters most: never leave the user with nothing that
    launches."""
    old = _build(tmp_path / "app", "WORKING")
    missing = tmp_path / "never-created" / "app"
    r = _run_helper(up._helper_script(old, missing, relaunch=False, pid=DEAD_PID),
                    tmp_path / "apply.sh")
    assert "restored" in r.stderr
    assert old.exists()
    assert (old / "marker.txt").read_text() == "WORKING"


def test_the_helper_waits_for_this_process_before_touching_anything(tmp_path):
    body = up._helper_script(tmp_path / "a", tmp_path / "b", relaunch=True,
                             pid=4242)
    assert "while kill -0 4242" in body
    assert body.index("while kill -0 4242") < body.index("mv ")


def test_apply_staged_refuses_a_source_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "is_frozen", lambda: False)
    with pytest.raises(up.UpdateError, match="source checkout"):
        up.apply_staged(tmp_path / "whatever")


def test_apply_staged_refuses_a_missing_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "is_frozen", lambda: True)
    monkeypatch.setattr(up, "install_root", lambda: tmp_path / "app")
    with pytest.raises(up.UpdateError, match="missing"):
        up.apply_staged(tmp_path / "not-there")


# --- install root detection ------------------------------------------------

def test_install_root_is_none_for_a_source_checkout(monkeypatch):
    monkeypatch.setattr(up, "is_frozen", lambda: False)
    assert up.install_root() is None


def test_install_root_finds_the_app_bundle(monkeypatch, tmp_path):
    app = tmp_path / "jobscout.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    exe = app / "jobscout"
    exe.write_text("x")
    monkeypatch.setattr(up, "is_frozen", lambda: True)
    monkeypatch.setattr(up.sys, "platform", "darwin")
    monkeypatch.setattr(up.sys, "executable", str(exe))
    assert up.install_root() == tmp_path / "jobscout.app"


def test_install_root_finds_the_onedir_folder(monkeypatch, tmp_path):
    d = tmp_path / "jobscout"
    d.mkdir()
    exe = d / "jobscout.exe"
    exe.write_text("x")
    monkeypatch.setattr(up, "is_frozen", lambda: True)
    monkeypatch.setattr(up.sys, "platform", "win32")
    monkeypatch.setattr(up.sys, "executable", str(exe))
    assert up.install_root() == d


# --- update(): the top-level flow -----------------------------------------

def test_update_refuses_a_missing_local_archive(tmp_path):
    with pytest.raises(up.UpdateError, match="no such file"):
        up.update(tmp_path, from_archive=tmp_path / "absent.zip")


def test_update_from_a_local_archive_stages_it(tmp_path):
    arc = tmp_path / "jobscout-macos-arm64.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("jobscout.app/Contents/MacOS/jobscout", "binary")
    res = up.update(tmp_path, from_archive=arc, apply=False)
    assert res["ok"] is True
    assert res["updated"] is False
    assert Path(res["staged"]).name == "jobscout.app"


def test_update_reports_being_current_rather_than_downloading(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "check", lambda *a, **k: {
        "ok": True, "update_available": False, "current": "0.1.0",
        "latest": "0.1.0"})
    res = up.update(tmp_path, apply=False)
    assert res["updated"] is False
    assert "already up to date" in res["reason"]


def test_update_says_so_when_the_release_has_no_build_for_this_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "check", lambda *a, **k: {
        "ok": True, "update_available": True, "current": "0.1.0", "latest": "0.2.0",
        "asset": None, "asset_for_platform": "jobscout-plan9-x86_64.zip",
        "url": "https://example.invalid/rel"})
    with pytest.raises(up.UpdateError, match="no build for this machine"):
        up.update(tmp_path, apply=False)


# --- the explicit two-step flow (download, then quit) ----------------------
#
# The app used to auto-quit after staging. Installing means terminating
# jobscout, and that should be something the user asks for, not a side effect
# of pressing a button labelled "install".

class _FakeCfg:
    root = None


def _jsapi(tmp_path):
    from jobscout.app import JsApi
    api = JsApi.__new__(JsApi)                 # no __init__: no real workspace
    api.cfg = _FakeCfg()
    api.cfg.root = tmp_path
    api._windows = []
    api._staged = None
    api._staged_version = None
    return api


def test_update_install_stages_but_does_not_quit(tmp_path, monkeypatch):
    api = _jsapi(tmp_path)
    monkeypatch.setattr(up, "install_root", lambda: tmp_path / "app")
    monkeypatch.setattr(up, "update", lambda *a, **k: {
        "ok": True, "updated": True, "staged": str(tmp_path / "staged.app"),
        "staged_version": "0.2.0"})
    quit_calls = []
    monkeypatch.setattr(api, "quit", lambda: quit_calls.append(1))

    res = api.update_install()
    assert res["ok"] is True
    assert res["staged_version"] == "0.2.0"
    assert quit_calls == [], "downloading must never terminate the app"


def test_a_second_install_reuses_the_staged_build(tmp_path, monkeypatch):
    """Choosing 'not now' and coming back must not re-download 45 MB."""
    api = _jsapi(tmp_path)
    staged = tmp_path / "staged.app"
    staged.mkdir()
    monkeypatch.setattr(up, "install_root", lambda: tmp_path / "app")
    calls = []
    monkeypatch.setattr(up, "update", lambda *a, **k: calls.append(1) or {})
    api._staged = str(staged)
    api._staged_version = "0.2.0"

    res = api.update_install()
    assert res["reused"] is True
    assert calls == [], "the network must not be touched again"


def test_update_quit_refuses_when_nothing_is_staged(tmp_path, monkeypatch):
    api = _jsapi(tmp_path)
    res = api.update_quit()
    assert res["ok"] is False
    assert "nothing has been staged" in res["error"]


def test_update_quit_spawns_the_helper_then_closes_the_window(tmp_path, monkeypatch):
    api = _jsapi(tmp_path)
    staged = tmp_path / "staged.app"
    staged.mkdir()
    api._staged = str(staged)
    spawned = []
    monkeypatch.setattr(up, "apply_staged",
                        lambda p, relaunch=True: spawned.append((p, relaunch)))
    closed = []
    monkeypatch.setattr(api, "quit", lambda: closed.append(1) or {"ok": True})

    res = api.update_quit()
    assert res["quitting"] is True
    assert spawned == [(staged, True)], "the helper must be started first"
    assert closed == [1], "and only then does the window close"


def test_a_source_checkout_is_told_to_git_pull(tmp_path, monkeypatch):
    api = _jsapi(tmp_path)
    monkeypatch.setattr(up, "install_root", lambda: None)
    res = api.update_install()
    assert res["ok"] is False
    assert "git pull" in res["error"]


# --- the helper must not wait forever -------------------------------------

def test_the_helper_bounds_how_long_it_waits(tmp_path):
    """A wait with no limit means a stuck helper is an unexplained hang and the
    update silently never lands."""
    body = up._helper_script(tmp_path / "a", tmp_path / "b", relaunch=False,
                             pid=DEAD_PID)
    assert str(up.WAIT_SECONDS) in body
    assert "did not exit" in body
    # and it must give up BEFORE touching the installed build
    assert body.index("did not exit") < body.index("mv ")


def test_the_helper_gives_up_without_touching_the_build(tmp_path):
    old = _build(tmp_path / "app", "STILL RUNNING")
    new = _build(tmp_path / "staged" / "app", "NEW")
    # a pid that IS alive: this process
    body = up._helper_script(old, new, relaunch=False, pid=os.getpid())
    body = body.replace(f"if [ \"$n\" -gt {up.WAIT_SECONDS} ]", 'if [ "$n" -gt 1 ]')
    r = _run_helper(body, tmp_path / "apply.sh")
    assert "did not exit" in r.stderr
    assert (old / "marker.txt").read_text() == "STILL RUNNING", "must be untouched"
    assert new.exists(), "the staged build must not be consumed"
