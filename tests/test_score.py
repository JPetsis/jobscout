from datetime import date, timedelta

from jobscout.models import Posting
from jobscout.score import prefs_hash, reasons_text, score_posting

PREFS = {
    "max_age_days": 21,
    "title_boost": 3, "title_cap": 6,
    "keyword_weight": 1, "keyword_cap": 8,
    "location_boost": 2, "remote_bonus": 1,
    "title_terms": ["engineer", "entwickler"],
    "keywords": ["python", "docker"],
    "exclude_terms": ["internship", "vertrieb"],
    "locations_allow": ["berlin", "remote"],
    "locations_deny": [],
    "require_location_match": False,
}


def mk(**kw):
    base = dict(source="t", title="Software Developer")
    base.update(kw)
    return Posting(**base)


def test_title_keywords_location_with_reasons():
    p = mk(title="Senior Python Engineer", location="Berlin", description="docker and python")
    sc = score_posting(PREFS, p)
    assert not sc.filtered
    assert sc.score == 3 + 2 + 2  # title:engineer + kw python,docker + loc:berlin
    labels = [r[0] for r in sc.reasons]
    assert "title:engineer" in labels
    assert "kw:python" in labels and "kw:docker" in labels
    assert "loc:berlin" in labels
    assert "why" not in reasons_text(sc)  # sanity: reasons_text returns pairs only


def test_title_cap():
    sc = score_posting(PREFS, mk(title="engineer entwickler", location="", description=""))
    assert sc.score == 6  # capped at title_cap


def test_keyword_cap():
    prefs = dict(PREFS, keyword_cap=1)
    sc = score_posting(prefs, mk(title="x", description="python docker"))
    assert sc.score == 1


def test_exclude_filters():
    sc = score_posting(PREFS, mk(title="Sales Manager", description="vertrieb und so"))
    assert sc.filtered and sc.reason.startswith("excluded:")


def test_stale_filters_when_date_known():
    old = (date.today() - timedelta(days=40)).isoformat()
    sc = score_posting(PREFS, mk(title="Python Engineer", posted_at=old))
    assert sc.filtered and sc.reason.startswith("stale:")


def test_recent_date_passes_and_unknown_date_kept():
    recent = (date.today() - timedelta(days=3)).isoformat()
    assert not score_posting(PREFS, mk(title="Python Engineer", posted_at=recent)).filtered
    assert not score_posting(PREFS, mk(title="Python Engineer", posted_at="")).filtered


def test_location_deny_and_require_match():
    prefs = dict(PREFS, locations_deny=["münchen"])
    sc = score_posting(prefs, mk(title="Python Engineer", location="München"))
    assert sc.filtered and sc.reason.startswith("location_deny:")

    prefs2 = dict(PREFS, require_location_match=True)
    sc2 = score_posting(prefs2, mk(title="Python Engineer", location="Hamburg"))
    assert sc2.filtered and sc2.reason == "location:no-match"

    sc3 = score_posting(prefs2, mk(title="Python Engineer", location="Berlin"))
    assert not sc3.filtered


def test_remote_bonus():
    sc = score_posting(PREFS, mk(title="Developer", location="Remote (EU)"))
    assert sc.score == 3  # loc:remote +2, remote bonus +1
    assert "remote" in [r[0] for r in sc.reasons]


def test_empty_prefs_never_filters():
    sc = score_posting({}, mk(title="Anything"))
    assert not sc.filtered and sc.score == 0


def test_title_deny_filters():
    prefs = dict(PREFS, title_deny=["lead", "praktikant"])
    sc = score_posting(prefs, mk(title="Lead Engineer", location=""))
    assert sc.filtered and sc.reason == "title_deny:lead"
    sc2 = score_posting(prefs, mk(title="Praktikant Software", location=""))
    assert sc2.filtered and sc2.reason == "title_deny:praktikant"


def test_stack_rules_title_and_description():
    prefs = dict(PREFS, keywords=[], stack_terms=["python", "laravel"],
                 stack_boost=2, stack_cap=4)
    sc = score_posting(prefs, mk(title="Python Entwickler", location="",
                                 description="laravel shop"))
    labels = [r[0] for r in sc.reasons]
    assert "stack:python" in labels and "stack:laravel" in labels
    assert sc.score == 3 + 4  # title:entwickler +3, stack capped at 4


def test_stack_min_hits_gate():
    prefs = dict(PREFS, stack_terms=["python", "laravel"], stack_min_hits=1)
    sc = score_posting(prefs, mk(title="Engineering Manager", location="",
                                 description="people and process"))
    assert sc.filtered and sc.reason.startswith("stack_min_hits")
    ok = score_posting(prefs, mk(title="Python Engineer", location="", description="laravel shop"))
    assert not ok.filtered


def test_description_terms_only_match_description():
    prefs = dict(PREFS, description_terms=["tdd"], description_weight=2, description_cap=4)
    sc = score_posting(prefs, mk(title="TDD Developer", location="", description=""))
    assert "desc:tdd" not in [r[0] for r in sc.reasons]
    sc2 = score_posting(prefs, mk(title="Developer", location="", description="we practise tdd"))
    assert "desc:tdd" in [r[0] for r in sc2.reasons]


def test_description_deny_filters():
    prefs = dict(PREFS, description_deny=["reisebereitschaft"])
    sc = score_posting(prefs, mk(title="Developer", location="",
                                 description="hohe reisebereitschaft erforderlich"))
    assert sc.filtered and sc.reason == "description_deny:reisebereitschaft"


def test_prefs_hash_is_order_independent():
    assert prefs_hash({"a": 1, "b": [2, 3]}) == prefs_hash({"b": [2, 3], "a": 1})
    assert prefs_hash({"a": 1}) != prefs_hash({"a": 2})


def test_compound_friendly_scoring_terms():
    # "entwickler" must hit the German compound "Softwareentwickler"
    prefs = dict(PREFS, keywords=[], title_terms=["software"], stack_terms=["entwickler"])
    sc = score_posting(prefs, mk(title="Softwareentwickler", location=""))
    assert "stack:entwickler" in [r[0] for r in sc.reasons]


def test_stack_needs_title_match():
    prefs = dict(PREFS, keywords=[], title_terms=[], stack_terms=["python"])
    sc = score_posting(prefs, mk(title="FP&A Manager", location="", description="we use python"))
    assert "stack:python" not in [r[0] for r in sc.reasons]
    prefs2 = dict(prefs, stack_needs_title_match=False)
    sc2 = score_posting(prefs2, mk(title="FP&A Manager", location="", description="we use python"))
    assert "stack:python" in [r[0] for r in sc2.reasons]
    prefs3 = dict(PREFS, keywords=[], title_terms=["engineer"], stack_terms=["python"])
    sc3 = score_posting(prefs3, mk(title="Data Engineer", location="", description="we use python"))
    assert "stack:python" in [r[0] for r in sc3.reasons]


def test_filters_are_whole_word_only():
    prefs = dict(PREFS, keywords=[], title_terms=[], stack_terms=[],
                 title_deny=["sales"])
    assert not score_posting(prefs, mk(title="Salesforce Developer", location="")).filtered
    sc = score_posting(prefs, mk(title="Sales Manager", location=""))
    assert sc.filtered and sc.reason == "title_deny:sales"


def test_scoring_terms_do_not_match_inside_words():
    prefs = dict(PREFS, keywords=[], title_terms=[], stack_terms=["git", "sql"])
    sc = score_posting(prefs, mk(title="Developer", location="",
                                 description="digital marketing tools"))
    labels = [r[0] for r in sc.reasons]
    assert "stack:git" not in labels
    assert "stack:sql" not in labels


def test_min_salary_filter_keeps_unknown_pay():
    prefs = dict(PREFS, min_salary=60000)
    low = mk(title="Python Engineer", salary_annual_max=45000)
    sc = score_posting(prefs, low)
    assert sc.filtered and sc.reason == "salary:below-60000"
    high = mk(title="Python Engineer", salary_annual_max=80000)
    assert not score_posting(prefs, high).filtered
    unknown = mk(title="Python Engineer")
    assert not score_posting(prefs, unknown).filtered   # unknown pay is kept
    off = mk(title="Python Engineer", salary_annual_max=1)
    assert not score_posting(dict(PREFS, min_salary=0), off).filtered


# --- stack_preferred: the 1-3 interest scale -------------------------------

WANT = dict(PREFS, keywords=[], stack_terms=[], stack_preferred={"php": 3, "python": 1})


def test_want_scores_by_own_weight():
    php = score_posting(WANT, mk(title="PHP Engineer", location=""))
    py = score_posting(WANT, mk(title="Python Engineer", location=""))
    assert "want:php" in [r[0] for r in php.reasons]
    assert php.score > py.score
    assert py.score > 0                       # python still counts, just less


def test_want_beats_equal_title_when_all_on_the_scale():
    # The scale only ranks technologies that are ON it. A wanted term at 3 beats
    # three table stakes at 1 (6 -> 3+1+1+1 = 6 with title, vs 3+3+3 = 9), but a
    # wanted term at 3 does NOT outrank three terms left on flat stack_terms at
    # 2 each — put table stakes on the scale at 1 to rank against them.
    prefs = dict(PREFS, keywords=[], title_terms=["engineer"], stack_terms=[],
                 stack_preferred={"php": 3, "laravel": 3,
                                  "javascript": 1, "typescript": 1, "react": 1},
                 stack_preferred_cap=9)
    php = score_posting(prefs, mk(title="Senior Engineer", location="",
                                  description="php and laravel"))
    js = score_posting(prefs, mk(title="Senior Engineer", location="",
                                 description="javascript typescript react"))
    assert php.score > js.score
    assert "want:php" in [r[0] for r in php.reasons]
    assert all(l.startswith(("title:", "want:")) for l, _ in js.reasons)


def test_want_is_order_independent():
    a = dict(PREFS, keywords=[], stack_terms=[],
             stack_preferred={"php": 3, "laravel": 3, "symfony": 2})
    b = dict(PREFS, keywords=[], stack_terms=[],
             stack_preferred={"symfony": 2, "laravel": 3, "php": 3})
    p = mk(title="Engineer", location="", description="php laravel symfony")
    assert score_posting(a, p).score == score_posting(b, p).score


def test_want_does_not_consume_stack_cap():
    # The wanted family keeps its points even when stack_cap is tiny and full.
    prefs = dict(PREFS, keywords=[], stack_terms=["rest", "api", "sql", "docker"],
                 stack_boost=2, stack_cap=2,
                 stack_preferred={"php": 3})
    sc = score_posting(prefs, mk(title="Engineer", location="",
                                 description="php rest api sql docker"))
    stack_pts = sum(p for l, p in sc.reasons if l.startswith("stack:"))
    want_pts = sum(p for l, p in sc.reasons if l.startswith("want:"))
    assert stack_pts == 2                    # stack_cap still applies to stack
    assert want_pts == 3                     # ...but not to the wanted family


def test_want_cap():
    prefs = dict(WANT, stack_preferred={"php": 3, "laravel": 3, "symfony": 3},
                 stack_preferred_cap=5)
    sc = score_posting(prefs, mk(title="Engineer", location="",
                                 description="php laravel symfony"))
    want_pts = sum(p for l, p in sc.reasons if l.startswith("want:"))
    assert want_pts == 5


def test_want_respects_title_relevance():
    # Same guard as stack_terms: no stack evidence on an unrelated title.
    prefs = dict(PREFS, keywords=[], title_terms=[], stack_preferred={"php": 3})
    sc = score_posting(prefs, mk(title="FP&A Manager", location="", description="php"))
    assert "want:php" not in [r[0] for r in sc.reasons]
    prefs2 = dict(prefs, stack_needs_title_match=False)
    sc2 = score_posting(prefs2, mk(title="FP&A Manager", location="", description="php"))
    assert "want:php" in [r[0] for r in sc2.reasons]


def test_want_ignores_bad_weights_without_crashing():
    prefs = dict(PREFS, keywords=[], stack_terms=[], stack_preferred="not-a-map")
    assert not score_posting(prefs, mk(title="PHP Engineer", location="")).filtered
    prefs2 = dict(PREFS, keywords=[], stack_terms=[],
                  stack_preferred={"php": "high", "python": 2})
    sc = score_posting(prefs2, mk(title="Python Engineer", location=""))
    assert "want:python" in [r[0] for r in sc.reasons]
    assert "want:php" not in [r[0] for r in sc.reasons]


def test_absent_want_block_behaves_exactly_as_before():
    sc = score_posting(PREFS, mk(title="Senior Python Engineer", location="Berlin",
                                 description="docker and python"))
    assert sc.score == 3 + 2 + 2
    assert all(not l.startswith("want:") for l, _ in sc.reasons)


# --- single source of truth: a term is paid for ONCE -----------------------

OVERLAP = dict(PREFS, keywords=[], title_terms=["engineer"], title_boost=3,
               title_cap=6, stack_terms=["php", "rest", "api"],
               stack_boost=2, stack_cap=8,
               stack_preferred={"php": 3}, stack_preferred_cap=9)


def test_term_in_both_lists_is_scored_once():
    sc = score_posting(OVERLAP, mk(title="Engineer", location="",
                                   description="php rest api"))
    labels = [r[0] for r in sc.reasons]
    assert labels.count("php") == 0 or "stack:php" not in labels
    assert "want:php" in labels and "stack:php" not in labels
    # weighted value wins, no +2 shadow for the same technology
    assert dict((l, p) for l, p in sc.reasons)["want:php"] == 3


def test_unscaled_stack_terms_keep_scoring_normally():
    sc = score_posting(OVERLAP, mk(title="Engineer", location="",
                                   description="php rest api"))
    labels = [r[0] for r in sc.reasons]
    assert "stack:rest" in labels and "stack:api" in labels   # still +2 each


def test_consolidation_removed_terms_from_flat_only_list():
    # everything on the scale, nothing left in stack_terms
    prefs = dict(OVERLAP, stack_terms=[],
                 stack_preferred={"php": 3, "rest": 1, "api": 1},
                 stack_preferred_cap=9)
    sc = score_posting(prefs, mk(title="Engineer", location="",
                                 description="php rest api"))
    got = dict((l, p) for l, p in sc.reasons)
    assert got["want:php"] == 3 and got["want:rest"] == 1 and got["want:api"] == 1
    assert not any(l.startswith("stack:") for l, _ in sc.reasons)


def test_single_source_beats_double_count_on_real_config_shape():
    # the shape in ~/.jobscout/config.yaml: php on BOTH lists at cap 9
    prefs = dict(PREFS, keywords=[], title_terms=["engineer"], title_boost=3,
                 title_cap=6, stack_terms=["php", "laravel", "symfony", "typo3",
                                           "rest", "api", "sql", "git"],
                 stack_boost=2, stack_cap=8,
                 stack_preferred={"php": 3, "laravel": 3, "symfony": 3,
                                  "typo3": 3, "rest": 1, "api": 1, "sql": 1,
                                  "git": 1},
                 stack_preferred_cap=9)
    sc = score_posting(prefs, mk(title="PHP Engineer", location="",
                                 description="php laravel symfony rest api sql git"))
    labels = [r[0] for r in sc.reasons]
    assert not any(l.startswith("stack:") for l in labels)   # all deduped away
    # 9 from the scale (cap), 3 from the title, and NOT the old 23
    assert sc.score == 3 + 9


# --- Proposal B: an uncapped scale makes the weights mean something ---------

def test_cap_zero_is_uncapped():
    prefs = dict(PREFS, keywords=[], title_terms=["engineer"], stack_terms=[],
                 stack_preferred={"php": 3, "laravel": 3, "symfony": 3, "typo3": 3},
                 stack_preferred_cap=0)
    sc = score_posting(prefs, mk(title="Engineer", location="",
                                 description="php laravel symfony typo3"))
    assert sum(p for l, p in sc.reasons if l.startswith("want:")) == 12


def test_absent_cap_is_uncapped():
    prefs = dict(PREFS, keywords=[], title_terms=["engineer"], stack_terms=[],
                 stack_preferred={"php": 3, "laravel": 3, "symfony": 3, "typo3": 3})
    sc = score_posting(prefs, mk(title="Engineer", location="",
                                 description="php laravel symfony typo3"))
    assert sum(p for l, p in sc.reasons if l.startswith("want:")) == 12


def test_rescaling_the_scale_actually_matters_when_uncapped():
    # the property the cap destroyed: 1-3 and 1-6 must score differently
    desc = "php laravel symfony typo3"
    three = dict(PREFS, keywords=[], title_terms=["engineer"], stack_terms=[],
                 stack_preferred={"php": 3, "laravel": 3, "symfony": 3, "typo3": 3})
    six = dict(three, stack_preferred={"php": 6, "laravel": 6, "symfony": 6, "typo3": 6})
    a = score_posting(three, mk(title="Engineer", location="", description=desc))
    b = score_posting(six, mk(title="Engineer", location="", description=desc))
    assert a.score == 3 + 12 and b.score == 3 + 24


def test_cap_still_honoured_when_set():
    prefs = dict(PREFS, keywords=[], title_terms=["engineer"], stack_terms=[],
                 stack_preferred={"php": 3, "laravel": 3, "symfony": 3, "typo3": 3},
                 stack_preferred_cap=9)
    sc = score_posting(prefs, mk(title="Engineer", location="",
                                 description="php laravel symfony typo3"))
    assert sum(p for l, p in sc.reasons if l.startswith("want:")) == 9


# --- Proposal C: a signal-only scale (table stakes simply absent) ----------

SIGNAL = dict(PREFS, keywords=[], title_terms=["engineer"], title_boost=3,
              title_cap=6, stack_terms=[],
              stack_preferred={"php": 3, "laravel": 3})


def test_absent_terms_are_invisible_not_zero():
    # python is NOT on the scale: it must contribute nothing at all
    php = score_posting(SIGNAL, mk(title="Senior Engineer", location="",
                                   description="php laravel"))
    py = score_posting(SIGNAL, mk(title="Senior Engineer", location="",
                                  description="python django"))
    assert php.score > py.score
    assert py.score == 3          # title only, no want points at all


def test_signal_only_lets_a_wanted_post_beat_a_broad_stack_one():
    broad = score_posting(SIGNAL, mk(title="Senior Engineer", location="",
                                     description="react typescript docker aws "
                                                 "kubernetes postgres redis rest api"))
    php = score_posting(SIGNAL, mk(title="Senior Engineer", location="",
                                   description="php laravel"))
    assert php.score > broad.score

def test_permanence_is_positive_only_and_capped():
    prefs = dict(PREFS, permanent_terms=["unbefristet", "permanent"],
                 permanent_boost=4, permanent_cap=4)
    sc = score_posting(prefs, mk(title="Python Engineer",
                                 description="Eine unbefristete Stelle, permanent."))
    # title:engineer +3, kw:python +1, one perm boost of 4 (capped)
    assert sc.score == 8
    assert [l for l, _ in sc.reasons].count("perm:unbefristet") == 1
    sc2 = score_posting(prefs, mk(title="Python Engineer",
                                  description="Die Stelle ist befristet."))
    assert sc2.score == 4                      # title + kw only, no perm points
    assert not any(l.startswith("perm:") for l, _ in sc2.reasons)


def test_academic_deny_matches_compounds_but_not_plain_dev_titles():
    prefs = dict(PREFS, academic_deny=["professur", "postdoc", "w2", "doktorand"])
    for title in ("W2-Professur für Software-Engineering",
                  "Universitätsprofessur für Informatik",
                  "Postdoc in Software Engineering",
                  "Doktorand:in Softwareentwicklung"):
        sc = score_posting(prefs, mk(title=title))
        assert sc.filtered and sc.reason.startswith("academic:"), title
    assert not score_posting(prefs, mk(title="Full Stack Softwareentwickler")).filtered


def test_temporary_marker_is_reason_only_and_never_fires_on_unbefristet():
    from jobscout.score import contract_label_from_reasons
    prefs = dict(PREFS, permanent_terms=["unbefristet"], temporary_terms=["befristet"],
                 permanent_boost=4, permanent_cap=4)
    sc = score_posting(prefs, mk(title="Python Engineer",
                                 description="Die Stelle ist befristet bis 2030."))
    assert ["tmp:befristet", 0] in sc.reasons
    assert sc.score == 4                       # title:engineer +3, kw:python +1
    assert contract_label_from_reasons(sc.reasons) == "tmp:befristet"
    sc2 = score_posting(prefs, mk(title="Python Engineer",
                                  description="Eine unbefristete Stelle."))
    assert not any(l.startswith("tmp:") for l, _ in sc2.reasons)
    assert contract_label_from_reasons(sc2.reasons) == "perm:unbefristet"


def test_festanstellung_is_the_at_de_boards_word_for_permanent():
    """karriere.at's keyfact reads "Anstellungsart: Vollzeit (Festanstellung)"
    and never says "unbefristet" or "permanent"; without this term every
    Austrian posting stayed pill-less even though the data was right there."""
    prefs = dict(PREFS, permanent_terms=["unbefristet", "permanent", "festanstellung"],
                 permanent_boost=4, permanent_cap=4)
    sc = score_posting(prefs, mk(title="Software Entwickler",
                                 description="Anstellungsart: Vollzeit (Festanstellung)"))
    assert ["perm:festanstellung", 4] in sc.reasons
    base = score_posting(dict(prefs, permanent_terms=[]),
                         mk(title="Software Entwickler",
                            description="Anstellungsart: Vollzeit (Festanstellung)")).score
    assert sc.score == base + 4           # the term adds exactly the boost
    # and it must not leak into the inverse marker path
    assert not any(str(l).startswith("tmp:") for l, _ in sc.reasons)
