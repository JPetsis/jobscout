import types

from jobscout.querygen import generate_queries, merge_queries

DEV = {
    "title_terms": ["software", "entwickler", "engineer", "developer"],
    "stack_preferred": {"php": 3, "laravel": 3, "symfony": 3, "typo3": 3, "wordpress": 2},
    "stack_terms": [],
}

ARCH = {
    "title_terms": ["archaeologist", "wissenschaftler", "researcher"],
    "stack_preferred": {"geophysics": 3, "gpr": 3, "gis": 2},
    "stack_terms": ["excel"],
}


def cfg(sources_cfg=None, prefs=None):
    return types.SimpleNamespace(sources_cfg=sources_cfg or {}, prefs=prefs or {})


def kws(queries):
    return [q.get("keywords") or "" for q in queries]


def test_non_software_profile_generates_its_own_queries():
    # The shareability case: a profile in another field must not inherit
    # software keywords, and must not get an empty funnel either.
    qs = kws(generate_queries(cfg(), "xing", ARCH))
    assert "archaeologist" in qs
    assert "geophysics" in qs
    assert not any("php" in k.lower() or "engineer" in k.lower() for k in qs)


def test_broad_query_is_always_first_when_capable():
    for src in ("stepstone", "xing", "stellenanzeigen", "karriere_at", "jobs_ch"):
        qs = kws(generate_queries(cfg(), src, DEV))
        assert qs[0] == "", f"{src} should sweep broadly first"


def test_broad_query_can_be_disabled():
    qs = kws(generate_queries(cfg({"xing": {"broad_query": False}}), "xing", DEV))
    assert "" not in qs


def test_non_query_driven_sources_get_nothing():
    for src in ("hn", "larajobs", "himalayas", "greenhouse", "lever", "smartrecruiters"):
        assert generate_queries(cfg(), src, DEV) == []


def test_stack_terms_become_queries_by_weight():
    # Weight >= stack_query_min (default 2) becomes a query; table stakes at 1
    # do not, so a big profile cannot explode the request budget.
    qs = kws(generate_queries(cfg(), "xing", DEV))
    assert "php" in qs and "laravel" in qs
    prefs = dict(DEV, stack_preferred={"php": 1, "python": 1, "react": 1},
                 stack_terms=[])
    assert kws(generate_queries(cfg(), "xing", prefs)) == ["", "software",
                                                            "entwickler", "engineer"]


def test_budget_is_respected():
    # per-source setting
    c = cfg({"xing": {"query_budget": 3, "broad_query": False}})
    assert len(generate_queries(c, "xing", DEV)) == 3
    assert generate_queries(cfg({"xing": {"query_budget": 0}}), "xing", DEV) == []
    # profile-level default, read from the prefs actually passed in
    prefs = dict(DEV, query_budget=2)
    assert len(generate_queries(cfg(), "xing", prefs)) == 2


def test_opt_out_per_source():
    assert generate_queries(cfg({"xing": {"generate_queries": False}}), "xing", DEV) == []


def test_generated_is_deterministic_and_de_duplicated():
    a = kws(generate_queries(cfg(), "xing", DEV))
    b = kws(generate_queries(cfg(), "xing", DEV))
    assert a == b
    assert len(a) == len(set(a))


def test_hand_written_queries_win_and_are_kept():
    hand = [{"keywords": "softwareentwickler", "location": "berlin", "pages": 1},
            {"keywords": "php", "location": "berlin", "pages": 2}]
    merged = merge_queries(cfg({"xing": {"queries": hand}}), "xing", DEV)
    assert merged[0]["keywords"] == "softwareentwickler"
    assert merged[1]["keywords"] == "php"
    # hand-written php is not repeated by the generator
    assert [q["keywords"] for q in merged].count("php") == 1


def test_no_leakage_between_profiles():
    assert not any("arch" in k.lower() for k in kws(generate_queries(cfg(), "xing", DEV)))
    assert not any("php" in k.lower() for k in kws(generate_queries(cfg(), "xing", ARCH)))


def test_multiword_and_oversized_terms_are_dropped():
    prefs = {"title_terms": ["software engineer", "x" * 40, "", "engineer"],
             "stack_preferred": {"php symfony": 3}, "stack_terms": []}
    qs = kws(generate_queries(cfg(), "xing", prefs))
    assert "engineer" in qs
    assert not any(" " in k for k in qs if k)
    assert not any(len(k) > 32 for k in qs if k)


def test_missing_or_broken_prefs_do_not_raise():
    for prefs in ({}, {"title_terms": None}, {"stack_preferred": "nope"},
                  {"stack_preferred": {"php": "high"}}, {"title_terms": [None, 3]}):
        assert isinstance(generate_queries(cfg(), "xing", prefs), list)