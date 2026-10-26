"""Startup checks that decide whether the window can exist at all.

The Windows path cannot be exercised on macOS, so the pieces around it are pure
and tested here: where a bundled installer is looked for, whether the offer is
honoured, and that declining installs nothing.
"""
from pathlib import Path

from jobscout import app


class TestTheBundledInstaller:
    def test_it_is_found_next_to_the_executable(self, monkeypatch, tmp_path):
        exe = tmp_path / "jobscout.exe"
        exe.write_bytes(b"MZ")
        installer = tmp_path / "MicrosoftEdgeWebView2Setup.exe"
        installer.write_bytes(b"MZ")
        monkeypatch.setattr(app.sys, "frozen", True, raising=False)
        monkeypatch.setattr(app.sys, "executable", str(exe))
        assert app._webview2_bootstrapper() == installer

    def test_a_source_run_ships_none(self, monkeypatch):
        monkeypatch.delattr(app.sys, "frozen", raising=False)
        assert app._webview2_bootstrapper() is None


class TestInstalling:
    def test_declining_installs_nothing(self, monkeypatch, tmp_path):
        installer = tmp_path / "setup.exe"
        installer.write_bytes(b"MZ")
        ran = []
        monkeypatch.setattr(app, "_ask_windows", lambda *a, **k: False)
        monkeypatch.setattr(app.subprocess, "run",
                            lambda *a, **k: ran.append(a))
        assert app._install_webview2(installer) is False
        assert ran == []

    def test_accepting_runs_it_silently_and_rechecks(self, monkeypatch, tmp_path):
        installer = tmp_path / "setup.exe"
        installer.write_bytes(b"MZ")
        calls = []
        monkeypatch.setattr(app, "_ask_windows", lambda *a, **k: True)
        monkeypatch.setattr(app.subprocess, "run",
                            lambda cmd, **kw: calls.append((cmd, kw)))
        monkeypatch.setattr(app, "_has_webview2", lambda: True)
        assert app._install_webview2(installer) is True
        assert calls and calls[0][0][0] == str(installer)
        assert calls[0][0][1:] == ["/silent", "/install"]

    def test_a_failed_install_reports_failure(self, monkeypatch, tmp_path):
        installer = tmp_path / "setup.exe"
        installer.write_bytes(b"MZ")
        monkeypatch.setattr(app, "_ask_windows", lambda *a, **k: True)
        monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: None)
        monkeypatch.setattr(app, "_has_webview2", lambda: False)
        assert app._install_webview2(installer) is False


class TestTheLinuxBackendMessage:
    """A missing system library must not be reported as a missing Qt."""

    def test_the_soname_is_read_out_of_the_error(self):
        exc = ImportError("libEGL.so.1: cannot open shared object file: "
                          "No such file or directory")
        assert app._missing_shared_library(exc) == "libEGL.so.1"

    def test_an_unrelated_import_error_names_no_library(self):
        assert app._missing_shared_library(ImportError("No module named 'qtpy'")) == ""

    def test_the_message_names_the_package_and_clears_qt(self):
        text = app._missing_library_message("libEGL.so.1")
        assert "libEGL.so.1" in text
        assert "sudo apt install libegl1" in text
        assert "Qt itself is included" in text

    def test_an_unknown_library_still_names_the_file(self):
        text = app._missing_library_message("libweird.so.9")
        assert "libweird.so.9" in text
        assert "apt install" not in text
