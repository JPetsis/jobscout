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


def test_get_value_ignores_inline_trailing_comments():
    """The bug this guards: `min_score: 6   # below this ...` read back as the
    STRING '6   # below this ...' instead of 6, so the settings screen rendered
    an empty number box for every annotated value."""
    text = ("preferences:\n"
            "  min_score: 6            # below this a posting is stored\n"
            "  max_age_days: 21        # days\n"
            "  country_allow: [de, at]  # two-letter codes\n"
            "  title_terms:\n"
            "    - software          # the trade\n"
            "    - entwickler\n"
            "  stack_preferred:\n"
            "    php: 3              # wanted most\n"
            "  stack_needs_title_match: true   # narrow the net\n")
    assert cfgwrite.get_value(text, "preferences.min_score") == 6
    assert isinstance(cfgwrite.get_value(text, "preferences.min_score"), int)
    assert cfgwrite.get_value(text, "preferences.max_age_days") == 21
    assert cfgwrite.get_value(text, "preferences.country_allow") == ["de", "at"]
    assert cfgwrite.get_value(text, "preferences.title_terms") == ["software", "entwickler"]
    assert cfgwrite.get_value(text, "preferences.stack_preferred") == {"php": 3}
    assert cfgwrite.get_value(text, "preferences.stack_needs_title_match") is True


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


# --- multi-line flow lists -------------------------------------------------
#
# The live config wraps `sources.enabled` across three lines for readability.
# Treating that as a single line corrupted the file: the new value landed on
# the first line and the continuation lines were left dangling, which is not
# valid YAML. Both directions had to learn about the span, and these use the
# real config's exact shape.

WRAPPED = """\
sources:
  # sources run in this order; remove entries to disable them
  enabled: [linkedin, stepstone, xing, stellenanzeigen, karriere_at, jobs_ch,
            arbeitnow, berlinstartupjobs, ams, eures, hn, remotive, wwr, remoteok, larajobs,
            workingnomads, nodesk, himalayas, greenhouse, ashby, recruitee, lever, workable, smartrecruiters, jobsat]

  linkedin:
    max_requests: 4
"""


def test_reading_a_wrapped_flow_list_returns_the_whole_list():
    value = cfgwrite.get_value(WRAPPED, "sources.enabled")
    assert isinstance(value, list), f"got {type(value).__name__}: {value!r}"
    assert len(value) == 25
    assert value[0] == "linkedin"
    assert value[-1] == "jobsat"
    assert value[5] == "jobs_ch"                  # from the FIRST line
    assert value[10] == "hn"                      # from a CONTINUATION line


def test_writing_a_wrapped_flow_list_produces_valid_yaml():
    import yaml
    out = cfgwrite.set_list(WRAPPED, "sources.enabled", ["xing", "hn"])
    parsed = yaml.safe_load(out)                  # must not raise
    assert parsed["sources"]["enabled"] == ["xing", "hn"]
    assert parsed["sources"]["linkedin"]["max_requests"] == 4   # sibling intact


def test_writing_a_wrapped_flow_list_leaves_no_dangling_lines():
    """The exact corruption: a replacement on line 1 with 'arbeitnow, ...'
    left orphaned on the next line."""
    out = cfgwrite.set_list(WRAPPED, "sources.enabled", ["xing"])
    assert "arbeitnow" not in out
    assert out.count("enabled:") == 1


def test_the_wrapped_shape_is_preserved_for_a_long_list():
    """A 200-character single line is valid YAML and a readability regression,
    so the original wrapping style comes back."""
    long_list = [f"board{i:02d}" for i in range(30)]
    out = cfgwrite.set_list(WRAPPED, "sources.enabled", long_list)
    import yaml
    assert yaml.safe_load(out)["sources"]["enabled"] == long_list
    body = [l for l in out.splitlines() if "board" in l]
    assert len(body) > 1, "should still be wrapped"
    # continuation lines align just after the '['
    assert body[1].startswith(" " * 12)


def test_a_short_list_collapses_back_to_one_line():
    out = cfgwrite.set_list(WRAPPED, "sources.enabled", ["xing", "hn"])
    line = next(l for l in out.splitlines() if "enabled:" in l)
    assert line.strip() == "enabled: [xing, hn]"


def test_wrapped_flow_round_trip_is_byte_identical():
    current = cfgwrite.get_value(WRAPPED, "sources.enabled")
    assert cfgwrite.set_list(WRAPPED, "sources.enabled", current) == WRAPPED


def test_a_single_line_flow_list_still_works():
    text = "sources:\n  enabled: [a, b]\n"
    assert cfgwrite.get_value(text, "sources.enabled") == ["a", "b"]
    assert cfgwrite.set_list(text, "sources.enabled", ["c"]) == "sources:\n  enabled: [c]\n"


def test_a_wrapped_flow_is_not_confused_by_a_comment():
    text = ("sources:\n"
            "  enabled: [a, b,   # why not\n"
            "            c]\n")
    assert cfgwrite.get_value(text, "sources.enabled") == ["a", "b", "c"]
    import yaml
    out = cfgwrite.set_list(text, "sources.enabled", ["x", "y"])
    assert yaml.safe_load(out)["sources"]["enabled"] == ["x", "y"]


def test_real_configs_wrapped_sources_key_survives_a_toggle(tmp_path):
    """End to end on a copy of the live config: the exact scenario that
    corrupted the lab file."""
    if not REAL.exists():
        pytest.skip("no real workspace config")
    import shutil as _shutil

    import yaml as _yaml

    copy = tmp_path / "config.yaml"
    _shutil.copyfile(REAL, copy)
    before = copy.read_text(encoding="utf-8")
    original = _yaml.safe_load(before)["sources"]["enabled"]
    assert len(original) > 1

    # turn one board off, then read it back
    cfgwrite.edit(copy, "list", "sources.enabled", original[1:])
    mid = _yaml.safe_load(copy.read_text(encoding="utf-8"))
    assert mid["sources"]["enabled"] == original[1:]

    # and back on again
    cfgwrite.edit(copy, "list", "sources.enabled", original)
    after = copy.read_text(encoding="utf-8")
    assert _yaml.safe_load(after)["sources"]["enabled"] == original
    assert _comments(after) == _comments(before)      # every comment survived


# --- concurrent writes must not corrupt the file ---------------------------
#
# The desktop window can fire several saves at once (a checkbox list saving per
# click). cfgwrite.edit is read-modify-write, and it used to truncate-then-write
# via write_text, so an interleaved reader could see a partial file and derive a
# replacement from it. That is how fragments of one section ended up duplicated
# into another in a real config.

def test_concurrent_writes_never_corrupt_the_file(tmp_path):
    import threading as _threading

    import yaml as _yaml

    p = _w(tmp_path)
    original = p.read_text(encoding="utf-8")
    errors = []

    def writer(n):
        try:
            for i in range(40):
                cfgwrite.edit(p, "scalar", "preferences.min_score", (n * 100 + i) % 90 + 1)
        except Exception as e:                                    # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    threads = [_threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    text = p.read_text(encoding="utf-8")
    parsed = _yaml.safe_load(text)                 # the file must still PARSE
    assert parsed is not None
    assert 1 <= parsed["preferences"]["min_score"] <= 90
    assert parsed["preferences"]["strong_threshold"] == 10          # siblings intact
    assert parsed["preferences"]["title_terms"] == ["software", "entwickler"]
    assert sorted(_comments(text)) == sorted(_comments(original))   # no comment lost


def test_a_concurrent_reader_never_sees_a_partial_file(tmp_path):
    """The specific hazard: write_text truncates first, so a reader could see a
    half-written config. os.replace is atomic, so readers see old or new."""
    import threading as _threading

    import yaml as _yaml

    p = _w(tmp_path)
    bad = []
    stop = _threading.Event()

    def reader():
        while not stop.is_set():
            try:
                _yaml.safe_load(p.read_text(encoding="utf-8"))
            except Exception as e:                                # noqa: BLE001
                bad.append(f"{type(e).__name__}: {e}")
                return

    r = _threading.Thread(target=reader)
    r.start()
    try:
        for i in range(60):
            cfgwrite.edit(p, "scalar", "preferences.min_score", (i % 90) + 1)
    finally:
        stop.set()
        r.join(timeout=10)

    assert not bad, f"a reader saw an invalid file: {bad}"


def test_no_temp_files_are_left_behind(tmp_path):
    p = _w(tmp_path)
    cfgwrite.edit(p, "scalar", "preferences.min_score", 9)
    leftovers = [f.name for f in tmp_path.iterdir() if f.name.startswith(".jobscout-")]
    assert leftovers == [], leftovers


# --- ensure_section --------------------------------------------------------
#
# set_scalar refuses to invent structure, and rightly. This is the one
# deliberate exception, so it is tested for the damage it could do: appending a
# section that is already there, or eating the file's comments.

def test_ensure_section_appends_a_missing_section():
    text = "preferences:\n  min_score: 6\n"
    out = cfgwrite.ensure_section(text, "ui", ["theme: dark"])
    assert "ui:\n  theme: dark\n" in out
    assert "preferences:\n  min_score: 6\n" in out


def test_ensure_section_is_a_no_op_when_the_section_exists():
    text = "preferences:\n  min_score: 6\n\nui:\n  theme: light\n"
    assert cfgwrite.ensure_section(text, "ui", ["theme: dark"]) == text


def test_ensure_section_does_not_mistake_a_comment_for_the_section():
    """A commented-out `# ui:` is documentation, not the section."""
    text = "# ui:\n#   theme: light\npreferences:\n  min_score: 6\n"
    out = cfgwrite.ensure_section(text, "ui", ["theme: dark"])
    assert out != text
    assert out.count("\nui:\n") == 1


def test_ensure_section_does_not_match_a_nested_key():
    """A `ui:` nested under something else is not a top-level section."""
    text = "preferences:\n  ui:\n    theme: light\n"
    out = cfgwrite.ensure_section(text, "ui", ["theme: dark"])
    assert out != text


def test_ensure_section_keeps_every_comment():
    text = "# top\npreferences:\n  # inline\n  min_score: 6   # trailing\n"
    out = cfgwrite.ensure_section(text, "ui", ["theme: dark"])
    for line in text.splitlines():
        assert line in out, line


def test_ensure_section_result_is_valid_yaml():
    import yaml
    text = "preferences:\n  min_score: 6\n"
    out = cfgwrite.ensure_section(text, "ui", ["theme: dark"])
    assert yaml.safe_load(out) == {"preferences": {"min_score": 6},
                                  "ui": {"theme": "dark"}}


def test_ensure_section_adds_the_missing_newline_first():
    """A file with no trailing newline must not swallow the new section."""
    out = cfgwrite.ensure_section("preferences:\n  min_score: 6", "ui",
                                  ["theme: dark"])
    import yaml
    assert yaml.safe_load(out)["ui"] == {"theme": "dark"}


def test_ensure_section_through_edit_is_atomic(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("preferences:\n  min_score: 6\n", encoding="utf-8")
    assert cfgwrite.edit(f, "section", "ui", ["theme: dark"]) is True
    assert "ui:" in f.read_text(encoding="utf-8")
    # and again: no second section, no change reported
    assert cfgwrite.edit(f, "section", "ui", ["theme: dark"]) is False
