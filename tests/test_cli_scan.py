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
