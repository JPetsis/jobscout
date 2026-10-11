"""Tests for the settings / detail surface added to the service layer."""
import textwrap

import pytest

from jobscout import api, cfgwrite


CONFIG = textwrap.dedent("""\
    # a documented config
    preferences:
      # titles become queries
      title_terms:
        - software
      title_deny:
        - sales

      country_allow: [de]
      locations_prefer:
        - berlin

      # strictness
      min_score: 6
      strong_threshold: 10
      max_age_days: 21
      query_budget: 6
      stack_preferred:
        php: 3
      stack_needs_title_match: true

    sources:
      enabled: [xing, stepstone]
    """)


@pytest.fixture()
def cfgfile(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG, encoding="utf-8")
    return p


# --- reading ---------------------------------------------------------------

def test_read_settings_returns_every_editable_key_with_a_value(cfgfile):
    rows = api.read_settings(cfgfile)
    by = {r["key"]: r for r in rows}
    assert len(rows) == len(api.SETTINGS)
    assert by["preferences.min_score"]["value"] == 6
    assert by["preferences.country_allow"]["value"] == ["de"]
    assert by["preferences.stack_preferred"]["value"] == {"php": 3}
    assert by["preferences.stack_needs_title_match"]["value"] is True
    # every row the UI renders must be self-describing
    for r in rows:
        assert r["label"] and r["group"] and r["help"]


def test_read_settings_tolerates_a_missing_key(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("preferences:\n  min_score: 6\n", encoding="utf-8")
    rows = api.read_settings(p)
    by = {r["key"]: r for r in rows}
    assert by["preferences.min_score"]["value"] == 6
    assert by["preferences.title_terms"]["value"] is None     # absent, not a crash


# --- validation: the app is a trust boundary -------------------------------

def test_rejects_an_unknown_key():
    with pytest.raises(ValueError, match="not an editable setting"):
        api.validate_setting("preferences.hack_me", 1)
    with pytest.raises(ValueError):
        api.validate_setting("preferences.min_score.evil", 1)


def test_rejects_wrong_types():
    with pytest.raises(ValueError, match="expects a number"):
        api.validate_setting("preferences.min_score", "high")
    with pytest.raises(ValueError, match="expects a number"):
        api.validate_setting("preferences.min_score", True)   # bool is not a number
    with pytest.raises(ValueError, match="expects a list"):
        api.validate_setting("preferences.title_terms", "software")
    with pytest.raises(ValueError, match="expects an object"):
        api.validate_setting("preferences.stack_preferred", ["php"])
    with pytest.raises(ValueError, match="true/false"):
        api.validate_setting("preferences.stack_needs_title_match", "yes")


def test_rejects_out_of_range_numbers():
    with pytest.raises(ValueError, match="between 0 and 100"):
        api.validate_setting("preferences.min_score", 1e9)
    with pytest.raises(ValueError, match="between 0 and 100"):
        api.validate_setting("preferences.strong_threshold", -5)
    with pytest.raises(ValueError, match="between 0 and 3650"):
        api.validate_setting("preferences.max_age_days", 999999)
    with pytest.raises(ValueError, match="finite"):
        api.validate_setting("preferences.min_score", float("nan"))


def test_rejects_bad_map_entries():
    with pytest.raises(ValueError, match="must be a number"):
        api.validate_setting("preferences.stack_preferred", {"php": "lots"})
    with pytest.raises(ValueError, match="non-empty"):
        api.validate_setting("preferences.stack_preferred", {"": 3})


def test_normalises_good_values():
    assert api.validate_setting("preferences.title_terms", [" php ", "php", ""]) == ["php", "php"]
    assert api.validate_setting("preferences.max_age_days", 30.0) == 30
    assert isinstance(api.validate_setting("preferences.max_age_days", 30.0), int)
    assert api.validate_setting("preferences.min_score", 7) == 7.0
    assert api.validate_setting("preferences.stack_preferred", {"PHP": 3}) == {"PHP": 3}


# --- writing ---------------------------------------------------------------

def test_write_setting_changes_the_value_and_keeps_comments(cfgfile):
    api.write_setting(cfgfile, "preferences.min_score", 8)
    text = cfgfile.read_text(encoding="utf-8")
    assert "min_score: 8" in text
    assert "# a documented config" in text
    assert "# strictness" in text
    assert "# titles become queries" in text


def test_write_setting_refuses_unknown_key_without_touching_the_file(cfgfile):
    before = cfgfile.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        api.write_setting(cfgfile, "preferences.nope", 1)
    assert cfgfile.read_text(encoding="utf-8") == before


def test_write_setting_survives_a_full_validate_then_write_cycle(cfgfile):
    """What the settings screen actually does, end to end."""
    import yaml

    for key, value in [
        ("preferences.min_score", 9),
        ("preferences.strong_threshold", 14),
        ("preferences.title_terms", ["php", "typo3"]),
        ("preferences.country_allow", ["de", "at"]),
        ("preferences.stack_preferred", {"php": 3, "typo3": 3}),
        ("preferences.stack_needs_title_match", False),
    ]:
        api.write_setting(cfgfile, key, value)

    parsed = yaml.safe_load(cfgfile.read_text(encoding="utf-8"))
    assert parsed["preferences"]["min_score"] == 9
    assert parsed["preferences"]["strong_threshold"] == 14
    assert parsed["preferences"]["title_terms"] == ["php", "typo3"]
    assert parsed["preferences"]["country_allow"] == ["de", "at"]
    assert parsed["preferences"]["stack_preferred"] == {"php": 3, "typo3": 3}
    assert parsed["preferences"]["stack_needs_title_match"] is False
    assert parsed["sources"]["enabled"] == ["xing", "stepstone"]   # untouched


def test_round_tripping_through_read_and_write_is_stable(cfgfile):
    """Read the screen's values, write them straight back: no drift, no loss."""
    before = cfgfile.read_text(encoding="utf-8")
    for row in api.read_settings(cfgfile):
        if row["value"] is not None:
            api.write_setting(cfgfile, row["key"], row["value"])
    assert cfgfile.read_text(encoding="utf-8") == before
