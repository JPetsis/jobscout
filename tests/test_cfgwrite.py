"""The safety property that matters: editing config.yaml must not destroy it."""
import shutil
from pathlib import Path

import pytest

from jobscout import cfgwrite

SAMPLE = """\
# jobscout config
# top-level documentation that must survive

preferences:
  # about the age cutoff
  max_age_days: 21
  min_score: 6
  strong_threshold: 10

  # --- title rules ---
  title_terms:
    - software
    - entwickler
  title_deny:
    - sales

  # the location gate
  country_allow: [de]
  locations_prefer:
    - berlin
    - münchen

  # interest scale
  stack_preferred:
    php: 3
    laravel: 3

sources:
  enabled: [xing, stepstone]
"""


def _w(tmp_path, text=SAMPLE) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def _comments(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip().startswith("#")]


# --- the headline property -------------------------------------------------

def test_set_scalar_preserves_all_comments(tmp_path):
    p = _w(tmp_path)
    before = p.read_text(encoding="utf-8")
    cfgwrite.edit(p, "scalar", "preferences.min_score", 7)
    after = p.read_text(encoding="utf-8")

    assert _comments(after) == _comments(before)      # every comment intact
    assert "min_score: 7" in after
    assert "max_age_days: 21" in after                # siblings untouched
    assert "strong_threshold: 10" in after
    assert "top-level documentation that must survive" in after


def test_noop_write_is_byte_identical(tmp_path):
    p = _w(tmp_path)
    before = p.read_text(encoding="utf-8")
    changed = cfgwrite.edit(p, "scalar", "preferences.min_score", 6)
    assert changed is False
    assert p.read_text(encoding="utf-8") == before


def test_changing_a_value_keeps_the_trailing_comment_parseable(tmp_path):
    """The bug this guards: rebuilding as `value`+comment without the gap gives
    `min_score: 7# note`, where the # stops being a comment and the value
    silently becomes the string '7# note'."""
    import yaml

    p = tmp_path / "c.yaml"
    p.write_text("preferences:\n"
                 "  min_score: 6            # about the age cutoff\n"
                 "  strong_threshold: 10\n", encoding="utf-8")
    cfgwrite.edit(p, "scalar", "preferences.min_score", 7)
    out = p.read_text(encoding="utf-8")
    assert "min_score: 7 " in out                     # a gap survives
    assert "# about the age cutoff" in out
    parsed = yaml.safe_load(out)
    assert parsed["preferences"]["min_score"] == 7    # int, not '7# about...'
    assert isinstance(parsed["preferences"]["min_score"], int)


def test_scalar_without_a_trailing_comment_stays_clean(tmp_path):
    p = _w(tmp_path)
    cfgwrite.edit(p, "scalar", "preferences.min_score", 7)
    out = p.read_text(encoding="utf-8")
    assert "  min_score: 7\n" in out                 # no stray padding
    assert "# about the age cutoff" in out           # the comment above survives


def test_it_also_catches_the_real_configs_inline_comments(tmp_path):
    """Your config uses `min_score: 6            # below this ...`."""
    import yaml

    p = tmp_path / "c.yaml"
    p.write_text("preferences:\n"
                 "  min_score: 6            # below this a posting is stored\n"
                 "  strong_threshold: 10\n", encoding="utf-8")
    cfgwrite.edit(p, "scalar", "preferences.min_score", 8)
    out = p.read_text(encoding="utf-8")
    parsed = yaml.safe_load(out)
    assert parsed["preferences"]["min_score"] == 8
    assert parsed["preferences"]["strong_threshold"] == 10
    assert "# below this a posting is stored" in out


def test_change_then_revert_is_byte_identical(tmp_path):
    p = _w(tmp_path)
    before = p.read_text(encoding="utf-8")
    cfgwrite.edit(p, "scalar", "preferences.min_score", 7)
    cfgwrite.edit(p, "scalar", "preferences.min_score", 6)
    assert p.read_text(encoding="utf-8") == before


def test_list_noop_is_byte_identical(tmp_path):
    p = _w(tmp_path)
    before = p.read_text(encoding="utf-8")
    changed = cfgwrite.edit(p, "list", "preferences.country_allow", ["de"])
    assert changed is False
    assert p.read_text(encoding="utf-8") == before


# --- unknown keys must never be invented -----------------------------------

def test_unknown_key_raises_and_never_appends(tmp_path):
    p = _w(tmp_path)
    before = p.read_text(encoding="utf-8")
    for dotted in ("preferences.nope", "nope.at_all", "preferences.title_terms.missing"):
        with pytest.raises(cfgwrite.ConfigEditError):
            cfgwrite.edit(p, "scalar", dotted, 1)
    assert p.read_text(encoding="utf-8") == before     # file untouched


def test_empty_path_raises(tmp_path):
    with pytest.raises(cfgwrite.ConfigEditError):
        cfgwrite.set_scalar(SAMPLE, "", 1)


# --- lists -----------------------------------------------------------------

def test_inline_flow_list_is_replaced_inline(tmp_path):
    p = _w(tmp_path)
    cfgwrite.edit(p, "list", "preferences.country_allow", ["de", "at"])
    out = p.read_text(encoding="utf-8")
    assert "country_allow: [de, at]" in out
    assert _comments(out) == _comments(SAMPLE)         # comments kept


def test_block_list_stays_block(tmp_path):
    p = _w(tmp_path)
    cfgwrite.edit(p, "list", "preferences.title_terms", ["software", "php", "laravel"])
    out = p.read_text(encoding="utf-8")
    assert "    - php\n" in out
    assert "    - laravel\n" in out
    assert "    - entwickler\n" not in out
    assert "  # --- title rules ---" in out


def test_empty_list_is_expressible(tmp_path):
    # e.g. clearing locations, or the stack_terms: [] shape
    p = _w(tmp_path)
    cfgwrite.edit(p, "list", "preferences.title_deny", [])
    out = p.read_text(encoding="utf-8")
    assert "- sales" not in out
    assert "title_deny:" in out


def test_list_inside_nested_block(tmp_path):
    p = _w(tmp_path)
    cfgwrite.edit(p, "list", "sources.enabled", ["xing"])
    out = p.read_text(encoding="utf-8")
    assert "enabled: [xing]" in out
    assert "preferences:" in out


# --- mappings --------------------------------------------------------------

def test_mapping_round_trip(tmp_path):
    p = _w(tmp_path)
    cfgwrite.edit(p, "mapping", "preferences.stack_preferred",
                  {"php": 3, "laravel": 3, "symfony": 3})
    out = p.read_text(encoding="utf-8")
    assert "php: 3" in out and "symfony: 3" in out
    assert out.count("laravel: 3") == 1
    assert "  # interest scale" in out


def test_mapping_can_shrink(tmp_path):
    p = _w(tmp_path)
    cfgwrite.edit(p, "mapping", "preferences.stack_preferred", {"php": 3})
    out = p.read_text(encoding="utf-8")
    assert "laravel" not in out
    assert "php: 3" in out


# --- values ----------------------------------------------------------------

def test_types_are_emitted_correctly(tmp_path):
    p = _w(tmp_path)
    cfgwrite.edit(p, "scalar", "preferences.max_age_days", 30)
    cfgwrite.edit(p, "scalar", "preferences.min_score", 6.0)
    out = p.read_text(encoding="utf-8")
    assert "max_age_days: 30" in out
    assert "min_score: 6\n" in out          # 6.0 renders as 6, not "6.0"


def test_unicode_survives(tmp_path):
    p = _w(tmp_path)
    cfgwrite.edit(p, "list", "preferences.locations_prefer", ["münchen", "köln"])
    out = p.read_text(encoding="utf-8")
    assert "münchen" in out and "köln" in out


def test_value_needing_quotes_gets_them(tmp_path):
    p = _w(tmp_path)
    cfgwrite.edit(p, "scalar", "preferences.min_score", "#not-a-number")
    out = p.read_text(encoding="utf-8")
    assert '"#not-a-number"' in out        # not swallowed as a YAML comment


# --- reading ---------------------------------------------------------------

def test_get_value_reads_scalars_lists_and_mappings():
    assert cfgwrite.get_value(SAMPLE, "preferences.min_score") == 6
    assert cfgwrite.get_value(SAMPLE, "preferences.country_allow") == ["de"]
    assert cfgwrite.get_value(SAMPLE, "preferences.title_terms") == ["software", "entwickler"]
    assert cfgwrite.get_value(SAMPLE, "preferences.stack_preferred") == {"php": 3, "laravel": 3}
    assert cfgwrite.get_value(SAMPLE, "sources.enabled") == ["xing", "stepstone"]


def test_get_value_missing_returns_none():
    assert cfgwrite.get_value(SAMPLE, "preferences.nope") is None
    assert cfgwrite.get_value(SAMPLE, "nothing.here") is None


# --- the real config (skipped when it does not exist, e.g. in CI) ----------

REAL = Path.home() / ".jobscout" / "config.yaml"


@pytest.mark.skipif(not REAL.exists(), reason="no real workspace config")
def test_real_config_survives_a_full_round_trip(tmp_path):
    """The strongest guarantee: every edit this UI can make, applied to a copy
    of the REAL config, leaves all comments intact and re-parses to the same
    structure."""
    import yaml

    copy = tmp_path / "config.yaml"
    shutil.copyfile(REAL, copy)
    original = copy.read_text(encoding="utf-8")
    before = yaml.safe_load(original)
    before_comments = _comments(original)
    assert len(before_comments) > 50, "expected a heavily documented config"

    # touch one key of each shape the settings screen can write
    cfgwrite.edit(copy, "scalar", "preferences.min_score",
                  before["preferences"]["min_score"])
    cfgwrite.edit(copy, "list", "preferences.country_allow",
                  before["preferences"]["country_allow"])
    cfgwrite.edit(copy, "list", "preferences.title_terms",
                  before["preferences"]["title_terms"])
    cfgwrite.edit(copy, "mapping", "preferences.stack_preferred",
                  before["preferences"].get("stack_preferred") or {"php": 3})
    after_text = copy.read_text(encoding="utf-8")

    assert after_text == original, "a same-value edit must not change one byte"
    assert _comments(after_text) == before_comments
    assert yaml.safe_load(after_text) == before
