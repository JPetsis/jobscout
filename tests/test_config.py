import pytest

from jobscout.config import load_config, source_settings


def test_init_creates_workspace_and_loads(ws):
    cfg = load_config()
    assert cfg.path.exists()
    assert cfg.path.name == "config.yaml"
    assert (cfg.root / "reports").is_dir()
    assert "linkedin" in cfg.enabled and "stepstone" in cfg.enabled
    assert "workable" not in cfg.enabled  # not in the default enabled list


def test_default_template_has_field_rules(ws):
    cfg = load_config()
    assert cfg.prefs.get("stack_min_hits") == 0
    assert "sales" in cfg.prefs.get("title_deny", [])
    assert "python" in cfg.prefs.get("stack_terms", [])
    assert cfg.prefs.get("require_location_match") is False
    assert cfg.prefs.get("min_score") == 6
    # 12 -> 10: the histogram breaks at 10 (192 rows at >=9, 21 at >=10), so 12
    # left almost nothing in the strong band.
    assert cfg.prefs.get("strong_threshold") == 10
    assert cfg.prefs.get("stack_needs_title_match") is True


def test_legacy_toml_still_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBSCOUT_HOME", str(tmp_path / "legacy"))
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "config.toml").write_text(
        '[preferences]\nmin_score = 7\n[sources]\nenabled = ["hn"]\n', encoding="utf-8")
    cfg = load_config()
    assert cfg.path.name == "config.toml"
    assert cfg.prefs["min_score"] == 7
    assert cfg.enabled == ["hn"]


def test_source_settings_merge(ws):
    cfg = load_config()
    li = source_settings(cfg, "linkedin")
    assert li["enabled"] is True
    assert li["max_requests"] == 24
    assert li["queries"] and "keywords" in li["queries"][0]

    gh = source_settings(cfg, "greenhouse")
    assert gh["enabled"] is True
    tokens = source_settings(cfg, "ashby")["companies"]
    assert "n8n" in tokens and "langfuse" in tokens

    unknown = source_settings(cfg, "lever")
    assert unknown["enabled"] is False
    assert unknown["companies"] == []


def test_load_missing_config_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBSCOUT_HOME", str(tmp_path / "nope"))
    with pytest.raises(SystemExit):
        load_config()


def test_init_is_idempotent(ws, monkeypatch):
    from jobscout.config import init_workspace

    created = init_workspace(ws)
    assert created == []  # nothing new
    cfg = load_config()
    assert cfg.enabled  # config still intact
