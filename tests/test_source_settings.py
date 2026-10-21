"""Per-source knobs in the settings pane.

The pane is data-driven, so adding a source knob is a table row - but only for a
key that is actually in the config: cfgwrite refuses to invent structure, and a
control that fails on save is worse than no control at all.
"""
from jobscout import api

BASE = """preferences:
  min_score: 6
sources:
  enabled: [himalayas, instaffo]
  himalayas:
    max_pages: 20
  instaffo:
    max_pages: 10
"""


def _write(tmp_path, text=BASE):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_only_knobs_present_in_the_config_get_a_row(tmp_path):
    rows = {r["key"]: r for r in api.read_settings(_write(tmp_path)) if r.get("source_knob")}
    assert set(rows) == {"sources.himalayas.max_pages", "sources.instaffo.max_pages"}
    assert rows["sources.instaffo.max_pages"]["value"] == 10
    assert rows["sources.instaffo.max_pages"]["label"] == "pages per scan"
    assert rows["sources.instaffo.max_pages"]["source_label"] == "Instaffo"


def test_the_knob_sits_with_the_board_list(tmp_path):
    rows = api.read_settings(_write(tmp_path))
    keys = [r["key"] for r in rows]
    assert keys.index("sources.instaffo.max_pages") == keys.index("sources.enabled") + 2
    assert rows[keys.index("sources.instaffo.max_pages")]["group"] == "Per source"


def test_out_of_range_values_are_rejected(tmp_path):
    import pytest

    for bad in (0, -5, 100000):
        with pytest.raises(ValueError):
            api.validate_setting("sources.instaffo.max_pages", bad)
    assert api.validate_setting("sources.instaffo.max_pages", 50) == 50
    assert isinstance(api.validate_setting("sources.himalayas.max_pages", 3.0), int)


def test_a_knob_writes_through_cfgwrite_and_keeps_comments(tmp_path):
    p = _write(tmp_path, BASE + "  # a comment that must survive\n  xing:\n    max_pages: 2\n")
    out = api.write_setting(p, "sources.instaffo.max_pages", 40)
    assert out["value"] == 40 and out["changed"] is True
    text = p.read_text(encoding="utf-8")
    assert "max_pages: 40" in text
    assert "a comment that must survive" in text
