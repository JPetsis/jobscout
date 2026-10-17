"""Every source belongs to a market group, and the groups are honest.

The Settings screen groups the board checkboxes by market (DACH, US, Europe,
global/remote, academic, per-company). That grouping is only useful if it is
COMPLETE: a board missing from the table would appear under "Other", which a
user reads as "miscellaneous" rather than "someone forgot".
"""
from collections import Counter

from jobscout import api
from jobscout.sources import (GROUPS, GROUP_ORDER, GROUP_OTHER, SOURCE_NAMES,
                              group_of)


def test_every_source_has_a_group():
    unassigned = [n for n in SOURCE_NAMES if group_of(n) == GROUP_OTHER]
    assert not unassigned, (
        f"{unassigned} have no market group. Add them to GROUPS in "
        "jobscout/sources/__init__.py — a new board must not land in Other.")


def test_the_group_table_only_names_real_sources():
    """A renamed or removed source must not leave a stale entry behind."""
    stale = sorted(set(GROUPS) - set(SOURCE_NAMES))
    assert not stale, f"grouped but not a source any more: {stale}"


def test_every_group_in_the_table_is_in_the_display_order():
    """GROUP_ORDER drives the order the sections appear in, so a group missing
    from it would be sorted to the end and look like an afterthought."""
    missing = sorted(set(GROUPS.values()) - set(GROUP_ORDER))
    assert not missing, f"in GROUPS but not GROUP_ORDER: {missing}"


def test_no_group_is_empty():
    counts = Counter(group_of(n) for n in SOURCE_NAMES)
    for g in GROUP_ORDER:
        if g == GROUP_OTHER:
            continue
        assert counts.get(g), f"group '{g}' has no sources"


def test_the_window_gets_the_group_with_every_option():
    rows = api.source_options([])
    assert len(rows) == len(SOURCE_NAMES)
    for r in rows:
        assert r["group"], f"{r['value']} has no group in the settings payload"
        assert r["group"] in GROUP_ORDER


def test_options_arrive_sorted_by_group():
    """The front-end renders sections in arrival order, so the backend decides
    the order; without this the sections would appear in registry order, which
    is roughly historical rather than by market."""
    rows = api.source_options([])
    rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    seen = [rank[r["group"]] for r in rows]
    assert seen == sorted(seen), [r["group"] for r in rows]


def test_the_dach_group_is_the_regional_boards():
    """Spot-check the assignment that matters most to a German user."""
    for name in ("stepstone", "xing", "karriere_at", "jobs_ch", "ams", "jobsat",
                 "jobware", "englishjobs"):
        assert group_of(name) == "DACH", name


def test_the_us_and_academic_groups_are_right():
    for name in ("themuse", "usajobs", "governmentjobs"):
        assert group_of(name) == "US", name
    for name in ("academics", "wissenschaftskarriere", "myscience"):
        assert group_of(name) == "Academic and research", name


def test_company_boards_are_the_ones_you_configure_yourself():
    """These need employer tokens in `companies:`, so they are grouped by that
    shared caveat rather than by market."""
    for name in ("greenhouse", "lever", "workday", "teamtailor", "ashby"):
        assert group_of(name) == "Company boards", name
