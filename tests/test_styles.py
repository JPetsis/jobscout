"""CSS rules that must not collide with each other.

A stylesheet has no scoping, so a later rule for the same class silently rewrites
everything that uses it. That is not a theoretical risk: the settings list editor
added a second `.chip` rule (10px left padding, 4px right) and every card pill in
the app started pushing its label to the right, while the fix for "the chips look
off-centre" appeared to do nothing because it was overridden further down.
"""
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "jobscout" / "web" / "styles.css").read_text(
    encoding="utf-8"
)


def _block(selector: str) -> str:
    start = CSS.index(selector)
    return CSS[start:CSS.index("}", start)]


def test_only_one_unscoped_chip_rule():
    starts = [ln.strip() for ln in CSS.splitlines()]
    assert sum(1 for ln in starts if ln.startswith(".chip {")) == 1, (
        "a second top-level `.chip` rule will win on source order and restyle "
        "every card pill"
    )


def test_the_label_pill_centres_its_text():
    block = _block(".chip {")
    assert "display: inline-flex" in block
    assert "align-items: center" in block
    assert "line-height: 1" in block
    # 3/3 was the "aligned right" bug (a later rule won). 2/4 is deliberate:
    # the descender takes room the eye does not count, so equal padding leaves
    # the x-height sitting low in the pill.
    assert "padding: 2px 8px 4px" in block


def test_the_settings_list_editor_is_scoped():
    assert ".chips .chip {" in CSS
    assert ".chips .chip-text {" in CSS
    assert "\n.chips .chip-x" in CSS or ".chips .chip-x," in CSS
