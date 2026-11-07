"""The fill job: it runs off the main thread and reports what it did."""
from __future__ import annotations

import time
import types

from jobscout import app as appmod, db as dbm, filler


def test_the_fill_job_runs_and_reports(tmp_path, monkeypatch):
    dbm.connect(tmp_path / "jobscout.db").close()
    cfg = types.SimpleNamespace(root=tmp_path, prefs={})

    monkeypatch.setattr(filler, "run",
                        lambda *a, **k: {"total": 3, "done": 3, "filled": 2, "parked": []})
    job = appmod.FillJob(cfg)
    assert job.start(limit=5)["started"] is True
    for _ in range(100):
        if not job.status()["running"]:
            break
        time.sleep(0.02)
    st = job.status()
    assert st["running"] is False
    assert st["filled"] == 2 and st["total"] == 3
    assert st["error"] is None


def test_a_second_start_is_refused_while_one_runs(tmp_path, monkeypatch):
    dbm.connect(tmp_path / "jobscout.db").close()
    cfg = types.SimpleNamespace(root=tmp_path, prefs={})
    release = []
    monkeypatch.setattr(filler, "run", lambda *a, **k: release.append(1) or {
        "total": 0, "done": 0, "filled": 0, "parked": []})
    job = appmod.FillJob(cfg)
    job.start()
    assert job.start()["started"] is False
    for _ in range(100):
        if not job.status()["running"]:
            break
        time.sleep(0.02)


def test_the_setting_exists_and_has_a_pane():
    from jobscout.api import SECTION_OF, SETTINGS
    keys = {r["key"] for r in SETTINGS}
    assert "preferences.fill_after_scan" in keys
    assert "preferences.fill_after_scan" in SECTION_OF
