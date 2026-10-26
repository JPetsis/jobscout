"""The job logger's front end and the bridge it calls.

The data layer is covered in test_applications.py. This file pins the pieces a
refactor could quietly drop: the pane exists, the header button opens it, the
detail pane grows the block, and the App class exposes every method the
JavaScript calls -- a missing bridge method is a runtime error in the packaged
app and nothing catches it at import time.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_window_has_the_pane_and_the_button():
    html = _text("jobscout", "web", "index.html")
    assert 'id="apps"' in html and 'id="apps-btn"' in html
    assert 'id="apps-close"' in html


def test_the_javascript_draws_the_block_and_the_list():
    js = _text("jobscout", "web", "app.js")
    for marker in ("applicationBlock", "openApps", "renderApps", "appRow(",
                   "refreshAppBlock", "loadAppsCount"):
        assert marker in js, marker
    assert "$('apps-btn').addEventListener('click', openApps)" in js
    assert "$('apps').hidden = true" in js, "every other pane must close it"


def test_the_detail_pane_asks_for_the_application():
    js = _text("jobscout", "web", "app.js")
    assert "pywebview.api.application(d.id)" in js
    assert "pywebview.api.save_application(d.id" in js
    assert "pywebview.api.add_application_note(d.id" in js


def test_applied_roles_stay_on_the_board():
    """The default filter must not hide what the logger just created."""
    js = _text("jobscout", "web", "app.js")
    html = _text("jobscout", "web", "index.html")
    assert "status: 'active'" in js
    assert '<option value="active" selected>' in html


class TestTheBridge:
    """Every method the JavaScript calls must exist on the App object."""

    def test_the_app_exposes_the_logger(self):
        src = _text("jobscout", "app.py")
        for method in ("def application(", "def save_application(",
                       "def add_application_note(", "def delete_application(",
                       "def applications(", "def application_stats("):
            assert method in src, method

    def test_the_schema_creates_the_tables(self):
        from jobscout import db as dbm
        import sqlite3
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "x.db")
            conn.executescript(dbm.SCHEMA)
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"applications", "application_events"} <= names
