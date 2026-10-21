"""The settings screen's shape: six panes, English labels, no dead rows.

The window used to render every setting in one column. That made all of them
visible and none of them findable, so each row now carries a `section` (which
pane) and `advanced` (whether it hides under "Advanced"). These tests pin the
contract the front-end relies on, and the English-only rule the user asked for:
the window is English throughout, board brand names aside.
"""
import re
from pathlib import Path

import pytest

from jobscout import api, sources

SECTIONS = {"profile", "boards", "wanted", "where", "matching", "updates"}


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    """Read the shipped template, so this tests a config a person really gets."""
    from jobscout.config import DEFAULT_CONFIG

    root = tmp_path_factory.mktemp("cfg")
    path = root / "config.yaml"
    path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return api.read_settings(path)


def test_every_row_knows_its_pane():
    for row in api.ALL_SETTINGS:
        section, advanced = api.SECTION_OF.get(row["key"], (None, None))
        if row.get("source_knob"):
            continue
        assert section in SECTIONS, f"{row['key']} has no pane"


def test_no_setting_is_orphaned():
    """A key in SETTINGS but not in SECTION_OF would silently land somewhere."""
    missing = [r["key"] for r in api.SETTINGS if r["key"] not in api.SECTION_OF]
    assert not missing, missing


def test_rows_carry_the_section_and_advanced_flag(rows):
    for row in rows:
        assert row["section"] in SECTIONS, row["key"]
        assert isinstance(row["advanced"], bool), row["key"]
        if row.get("source_knob"):
            assert row["section"] == "boards"
            assert row.get("source"), row["key"]


def test_the_advanced_rows_are_the_rare_ones():
    advanced = {r["key"] for r in api.ALL_SETTINGS
                if api.SECTION_OF.get(r["key"], ("", False))[1]}
    assert advanced == {
        "preferences.max_results", "preferences.insider_days",
        "preferences.query_budget", "preferences.scan_if_older_hours",
    }, "the everyday surface must not drown in knobs"


def test_labels_and_help_are_english():
    """No umlauts, no German function words: brand names are the only exception."""
    german = re.compile(r"[äöüßÄÖÜ]|\b(und|oder|nicht|für|kein|wird)\b", re.I)
    for row in api.ALL_SETTINGS:
        text = f"{row['label']} {row['help']}"
        assert not german.search(text), (row["key"], text)


def test_pane_titles_are_english():
    app = Path(__file__).resolve().parents[1] / "jobscout" / "web" / "app.js"
    text = app.read_text(encoding="utf-8")
    for title in ("Job boards", "What you want", "Where", "Matching", "Updates",
                  "Profile"):
        assert f"title: '{title}'" in text, title
    assert "Search settings" in text


def test_the_window_renders_a_rail_and_board_cards():
    """A regression to one long column would drop these, so pin them."""
    app = Path(__file__).resolve().parents[1] / "jobscout" / "web" / "app.js"
    text = app.read_text(encoding="utf-8")
    for marker in ("set-rail", "set-markets", "set-boards", "boardCard(",
                   "chipsControl", "skillsControl", "set-adv"):
        assert marker in text, marker
    assert "s-boardgroup" not in text, "the old wall of checkboxes came back"


def test_market_group_names_are_spelled_out():
    names = [
        sources.GROUP_DACH, sources.GROUP_US, sources.GROUP_EU,
        sources.GROUP_GLOBAL, sources.GROUP_ACADEMIC, sources.GROUP_COMPANY,
    ]
    assert sources.GROUP_DACH == "Germany, Austria & Switzerland"
    assert all(len(n) > 2 for n in names)
