"""Goals: one standalone target at a time, always optional, never a bundle."""
from jobscout.goals import Goal, counts_by_goal, load_goals, match


class Row(dict):
    """Stands in for a sqlite3.Row: goals only need a few fields."""
    def __getitem__(self, k):
        return dict.__getitem__(self, k)


def _row(title="Senior PHP Developer", company="Acme", location="Vienna, Austria",
         description="", score=14, annual=None):
    return Row(id=1, title=title, company=company, location=location,
               description=description, score=score, salary_annual_max=annual)


# ---------------------------------------------------------------- parsing

def test_no_goals_block_is_empty_and_optional():
    assert load_goals({}) == {}
    assert load_goals({"goals": None}) == {}
    # goals must never break config loading
    assert load_goals({"goals": "austria"}) == {}


def test_loads_standalone_goals():
    goals = load_goals({"goals": {
        "austria": {"kind": "location", "locations": ["austria", "wien"],
                    "label": "Austria"},
        "senior": {"kind": "seniority", "terms": ["senior", "lead"]},
    }})
    assert set(goals) == {"austria", "senior"}
    assert goals["austria"].kind == "location"
    assert goals["senior"].terms == ["senior", "lead"]


def test_malformed_goal_is_skipped_not_fatal():
    goals = load_goals({"goals": {
        "good": {"kind": "location", "locations": ["wien"]},
        "bad_salary": {"kind": "salary", "salary_min": "not-a-number"},
        "not_a_dict": 42,
    }})
    assert set(goals) == {"good"}


def test_shorthand_string_goal():
    goals = load_goals({"goals": {"austria": "Austria"}})
    assert goals["austria"].label == "Austria"


# ---------------------------------------------------------------- matching

def test_location_goal_matches_austria():
    g = Goal(name="austria", kind="location", locations=["austria", "österreich", "wien"])
    assert match(_row(location="Vienna, Austria"), g)
    assert match(_row(location="Wien, Österreich"), g)
    assert not match(_row(location="Berlin, Germany"), g)


def test_austria_and_duesseldorf_cannot_be_one_goal():
    """The bug this feature exists to prevent: two countries in one goal."""
    # 'austria' as a standalone location goal must not match a German posting
    g = Goal(name="austria", kind="location", locations=["austria", "österreich"])
    assert not match(_row(location="Düsseldorf, Germany"), g)
    # and a goal naming a single city is equally standalone
    g2 = Goal(name="düsseldorf", kind="location", locations=["düsseldorf", "duesseldorf"])
    assert match(_row(location="Düsseldorf, Germany"), g2)
    assert not match(_row(location="Vienna, Austria"), g2)


def test_seniority_goal_is_title_scoped_by_default():
    # a body mention like "lead a team" is NOT a seniority signal: matching it
    # buried real senior roles under false positives (Tether, Field Service
    # Engineer etc. all matched on prose). Title-only is the default for that.
    g = Goal(name="senior", kind="seniority", terms=["senior"])
    assert match(_row(title="Senior PHP Developer"), g)
    assert not match(_row(title="PHP Developer",
                          description="You will lead a senior team"), g)


def test_scope_all_lets_a_goal_read_the_body():
    # for topic goals rather than rank, scope: all is the right choice
    g = Goal(name="typo3anywhere", kind="terms", terms=["typo3"], scope="all")
    assert match(_row(title="PHP Developer", description="we use TYPO3"), g)
    assert match(_row(title="TYPO3 Developer"), g)


def test_terms_goal_does_not_read_description_by_default():
    g = Goal(name="typo3", kind="terms", terms=["typo3"])
    assert match(_row(title="TYPO3 Developer"), g)
    # default scope is title/company, so a body mention must not match
    assert not match(_row(title="PHP Developer", description="we use TYPO3"), g)


def test_scope_is_loaded_from_config():
    goals = load_goals({"goals": {"g": {"kind": "terms", "terms": ["x"],
                                       "scope": "all"}}})
    assert goals["g"].scope == "all"
    assert load_goals({"goals": {"g": {"kind": "terms", "terms": ["x"]}}})["g"].scope == "title"


def test_salary_goal_never_matches_unknown_pay():
    g = Goal(name="paying", kind="salary", salary_min=70000)
    assert match(_row(annual=80000), g)
    assert match(_row(annual=70000), g)          # floor is inclusive
    assert not match(_row(annual=69999), g)
    # no number is not "enough"
    assert not match(_row(annual=None), g)


def test_disabled_goal_matches_nothing():
    g = Goal(name="austria", kind="location", locations=["austria"], enabled=False)
    assert not match(_row(location="Vienna, Austria"), g)


def test_min_score_is_an_extra_bar_not_a_replacement():
    g = Goal(name="austria", kind="location", locations=["austria"], min_score=12)
    assert match(_row(location="Vienna, Austria", score=14), g)
    assert not match(_row(location="Vienna, Austria", score=8), g)


def test_no_goal_object_is_never_a_match():
    assert not match(_row(), None)


def test_matching_is_case_and_space_insensitive():
    g = Goal(name="austria", kind="location", locations=["Austria"])
    assert match(_row(location="VIENNA,   AUSTRIA"), g)
