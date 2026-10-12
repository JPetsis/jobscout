"""Tests for the settings / detail surface added to the service layer."""
import textwrap

import pytest
import yaml

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


# --- which job boards are enabled ------------------------------------------
#
# `sources.enabled` uses the same SETTINGS/kind mechanism as everything else,
# with an `options` list the backend supplies. A typo is rejected rather than
# skipped with a warning at scan time, because a board that quietly returns
# nothing is indistinguishable from a board with no jobs.

def test_sources_enabled_is_an_editable_setting_with_options(cfgfile):
    rows = api.read_settings(cfgfile)
    row = next(r for r in rows if r["key"] == "sources.enabled")
    assert row["value"] == ["xing", "stepstone"]
    assert row["options"], "the UI needs the full list, not just what is on"
    by = {o["value"]: o for o in row["options"]}
    assert by["xing"]["checked"] is True
    assert by["stepstone"]["checked"] is True
    # a source in the registry but not in the config is offered, unticked
    assert by["linkedin"]["checked"] is False
    assert row["group"] == "Where to look"


def test_every_registry_source_is_offered(cfgfile):
    from jobscout.sources import SOURCE_NAMES
    row = next(r for r in api.read_settings(cfgfile) if r["key"] == "sources.enabled")
    assert [o["value"] for o in row["options"]] == SOURCE_NAMES


def test_options_carry_human_labels(cfgfile):
    row = next(r for r in api.read_settings(cfgfile) if r["key"] == "sources.enabled")
    for o in row["options"]:
        assert o["label"], f"{o['value']} has no LABEL"
    labels = {o["value"]: o["label"] for o in row["options"]}
    assert labels["xing"] != "xing"          # a real label, not the key again


def test_unknown_source_names_are_rejected():
    with pytest.raises(ValueError, match="not a source this build knows"):
        api.validate_setting("sources.enabled", ["xing", "linkedin", "nope"])
    with pytest.raises(ValueError, match="not a source"):
        api.validate_setting("sources.enabled", ["XING"])     # case matters


def test_known_source_names_are_accepted_and_deduped_identically():
    assert api.validate_setting("sources.enabled", ["xing", "linkedin"]) == ["xing", "linkedin"]
    assert api.validate_setting("sources.enabled", [" xing "]) == ["xing"]
    assert api.validate_setting("sources.enabled", ["xing", "xing"]) == ["xing", "xing"]


def test_an_empty_selection_is_allowed_but_expressible(cfgfile):
    """Turning everything off is a legitimate thing to want; the UI warns."""
    api.write_setting(cfgfile, "sources.enabled", [])
    parsed = yaml.safe_load(cfgfile.read_text(encoding="utf-8"))
    assert parsed["sources"]["enabled"] == []


def test_toggling_a_source_writes_it_and_keeps_comments(cfgfile):
    api.write_setting(cfgfile, "sources.enabled", ["xing", "linkedin", "hn"])
    text = cfgfile.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert parsed["sources"]["enabled"] == ["xing", "linkedin", "hn"]
    assert "# a documented config" in text
    assert parsed["preferences"]["min_score"] == 6         # siblings untouched


def test_reading_then_writing_the_source_list_is_a_no_op(cfgfile):
    """Opening Settings and closing it must not reorder or rewrite the list."""
    before = cfgfile.read_text(encoding="utf-8")
    row = next(r for r in api.read_settings(cfgfile) if r["key"] == "sources.enabled")
    api.write_setting(cfgfile, "sources.enabled", row["value"])
    assert cfgfile.read_text(encoding="utf-8") == before


def test_the_written_value_actually_drives_the_scanner_loader(cfgfile):
    """End of the chain: what the UI saves is what iter_sources will walk."""
    import yaml as _yaml
    from jobscout.sources import iter_sources
    api.write_setting(cfgfile, "sources.enabled", ["xing", "hn"])
    enabled = _yaml.safe_load(cfgfile.read_text(encoding="utf-8"))["sources"]["enabled"]
    assert [name for name, _ in iter_sources(enabled)] == ["xing", "hn"]


# --- the theme --------------------------------------------------------------
#
# A display preference: it must never be able to stop the window from opening,
# and it must be storable in a config written before ui.theme existed.

def test_theme_defaults_to_system(tmp_path):
    from jobscout import api
    f = tmp_path / "config.yaml"
    f.write_text("preferences:\n  min_score: 6\n", encoding="utf-8")
    assert api.read_theme(f) == "system"


def test_a_missing_config_does_not_raise(tmp_path):
    from jobscout import api
    assert api.read_theme(tmp_path / "nope.yaml") == "system"


def test_a_nonsense_stored_theme_falls_back_to_system(tmp_path):
    from jobscout import api
    f = tmp_path / "config.yaml"
    f.write_text("ui:\n  theme: chartreuse\n", encoding="utf-8")
    assert api.read_theme(f) == "system"


def test_theme_round_trips(tmp_path):
    from jobscout import api
    f = tmp_path / "config.yaml"
    f.write_text("preferences:\n  min_score: 6\n", encoding="utf-8")
    api.write_theme(f, "dark")
    assert api.read_theme(f) == "dark"
    api.write_theme(f, "light")
    assert api.read_theme(f) == "light"


def test_writing_a_theme_creates_the_section_in_an_older_config(tmp_path):
    """Your config predates ui:, and set_scalar will not invent structure."""
    from jobscout import api
    import yaml
    f = tmp_path / "config.yaml"
    original = "preferences:\n  min_score: 6   # keep me\n"
    f.write_text(original, encoding="utf-8")

    api.write_theme(f, "dark")

    text = f.read_text(encoding="utf-8")
    assert yaml.safe_load(text)["ui"]["theme"] == "dark"
    assert "keep me" in text


def test_writing_a_theme_twice_does_not_grow_the_file(tmp_path):
    import pathlib as _p
    from jobscout import api
    f = tmp_path / "config.yaml"
    f.write_text("preferences:\n  min_score: 6\n", encoding="utf-8")
    api.write_theme(f, "dark")
    n = len(f.read_text(encoding="utf-8").splitlines())
    api.write_theme(f, "light")
    api.write_theme(f, "dark")
    assert len(f.read_text(encoding="utf-8").splitlines()) == n


def test_a_bad_theme_is_refused(tmp_path):
    import pytest
    from jobscout import api
    f = tmp_path / "config.yaml"
    f.write_text("preferences:\n  min_score: 6\n", encoding="utf-8")
    with pytest.raises(ValueError):
        api.write_theme(f, "chartreuse")
    assert "ui:" not in f.read_text(encoding="utf-8"), "a refused write must not touch the file"


def test_the_template_ships_the_ui_section():
    """A fresh install must have somewhere to store the theme without a
    create-on-demand step, and both paths come from one source."""
    from jobscout.config import DEFAULT_CONFIG, UI_SECTION_BODY
    import yaml
    assert yaml.safe_load(DEFAULT_CONFIG)["ui"]["theme"] == "system"
    assert any("theme:" in line for line in UI_SECTION_BODY)
