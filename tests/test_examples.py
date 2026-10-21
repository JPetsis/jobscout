"""The shipped example profile has to stay true to what it claims.

examples/config-us-react.yaml is a starting point for a different person, so it
is a product surface: if a template change renames a key or an example edit
drifts, the file quietly stops meaning what it says. This pins the claims in its
own header: JS/TS/React titles, the US as a gate, New York as a preference.
"""
from pathlib import Path

import pytest

from jobscout.config import load_config
from jobscout.models import Posting
from jobscout.score import score_posting

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "config-us-react.yaml"


@pytest.fixture(scope="module")
def prefs(tmp_path_factory):
    root = tmp_path_factory.mktemp("example")
    (root / "config.yaml").write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return load_config(root)


def _score(cfg, title, location, description=""):
    p = Posting(source="x", title=title, company="ACME", location=location,
                url="https://x/1", external_id="1", description=description)
    return score_posting(cfg.prefs, p)


def test_the_example_parses_and_enables_only_known_sources(prefs):
    from jobscout.sources import SOURCE_NAMES
    assert prefs.enabled, "the example must scan something"
    unknown = [n for n in prefs.enabled if n not in SOURCE_NAMES]
    assert not unknown, f"the example enables sources this build does not have: {unknown}"


def test_it_is_a_us_profile_with_new_york_first(prefs):
    assert prefs.prefs["country_allow"] == ["us"]
    prefer = prefs.prefs["locations_prefer"]
    assert prefer[0] == "new york" and "brooklyn" in prefer
    assert prefs.prefs["require_title_match"] is True
    stack = prefs.prefs["stack_preferred"]
    assert stack["react"] == 3 and stack["typescript"] == 3


def test_a_new_york_react_role_is_a_strong_match(prefs):
    sc = _score(prefs, "Senior React Engineer", "New York, NY, United States",
                "React, TypeScript and Next.js.")
    assert not sc.filtered
    assert sc.score >= 10, "react + typescript + the city preference should clear the strong line"


def test_another_us_city_still_matches_but_ranks_lower(prefs):
    ny = _score(prefs, "React Engineer", "New York, NY, United States")
    tx = _score(prefs, "React Engineer", "Austin, TX, United States")
    assert not tx.filtered, "New York is a preference, not a gate"
    assert tx.score < ny.score


def test_a_non_us_role_is_filtered(prefs):
    sc = _score(prefs, "Senior React Engineer", "Berlin, Germany")
    assert sc.filtered


def test_a_non_react_stack_without_a_title_hit_is_not_a_match(prefs):
    assert _score(prefs, "PHP Developer", "New York, NY, United States").filtered
    assert _score(prefs, "Sales Engineer", "New York, NY, United States").filtered
