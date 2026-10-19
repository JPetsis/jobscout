"""Layout invariants the Python suite cannot see, so they are pinned as source.

Every check here exists because the rendered page showed something the tests
said was fine. A CSS regression is invisible to pytest -- there is no browser in
CI -- so the honest guard is a small, specific assertion on the rule that broke,
plus the preview harness for the eye. Each test names the defect it prevents.

The real verification is `make_preview.py` + looking at it; these are the trip
wires that fire before somebody has to look.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "jobscout" / "web" / "styles.css").read_text(encoding="utf-8")
JS = (ROOT / "jobscout" / "web" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "jobscout" / "web" / "index.html").read_text(encoding="utf-8")


def test_the_contract_chip_shows_one_canonical_word():
    """The chip used to repeat the matched phrase, so the same fact read
    "unbefristet" on one card, "Festanstellung" on the next and "permanent" on a
    third, in a window that is otherwise English. The prefix-stripping hack that
    lived here is gone: api.posting_dict now decides the word in Python, and the
    matched phrase stays in the reasons."""
    assert "r.contract" in JS
    assert "cmark.replace(/^(perm|tmp):/" not in JS, "the old strip is gone"
    assert "cmark.slice(5)" not in JS


def test_a_chip_never_wraps_inside_itself():
    """Six labels on a narrow card: the row may wrap between them, never inside
    one. Without nowrap they broke across two lines, and without flex: 0 0 auto
    they shrank until the text no longer fit its own box."""
    top = CSS.split(".top {", 1)[1].split("}", 1)[0]
    assert "flex-wrap: wrap" in top, "the row must be able to wrap"
    rule = CSS.split(".top > .chip,", 1)[1].split("}", 1)[0]
    assert "white-space: nowrap" in rule
    assert "flex: 0 0 auto" in rule


def test_the_columns_fill_the_whole_width():
    """auto-fill with minmax(320px, 1fr) parks every card at roughly 320px, so a
    maximised window produced MORE narrow cards instead of bigger ones. And a
    max-width on the grid left a bare strip on the right of a wide window, which
    is the thing this pins: 1fr columns share everything they are given."""
    import re
    grid = CSS.split("\n.grid {", 1)[1].split("}", 1)[0]
    # strip the comments first: the explanatory one mentions max-width while
    # explaining why there is none, and asserting on the raw block tripped on it
    decls = re.sub(r"/\*.*?\*/", "", grid, flags=re.S)
    assert "minmax(min(100%, 460px), 1fr)" in decls, "the cards must get wider"
    # the min can never exceed the window, or a narrow window overflows
    assert "min(100%," in decls
    assert "max-width" not in decls, "a cap leaves a strip of empty window"


def test_a_filter_label_may_wrap_instead_of_being_cut_off():
    # the RULE, not ".chip.direct.fresh {" which comes first in the file
    fresh = CSS.split("\n.fresh {", 1)[1].split("}", 1)[0]
    assert "white-space: normal" in fresh
    assert "max-width: 100%" in fresh


def test_the_filters_are_the_controls_the_tests_look_for():
    """The layout tests above name these; a rename would make them vacuous."""
    for control in ('id="q"', 'id="qtext"', 'id="fresh"', 'id="closing"',
                    'id="pay-words"', 'id="pay-city"'):
        assert control in HTML, f"{control} is gone"
