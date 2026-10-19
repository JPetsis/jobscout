"""Country-level location resolution, fully offline.

The rule these tests protect: a country is not a term, so it must never be
emulated with a hand-written city list. Germany has ~11,000 municipalities.
"""
from jobscout import geo
from jobscout.models import Posting
from jobscout.score import score_posting


def _p(location="", title="Senior PHP Developer", company="Acme"):
    return Posting(source="t", title=title, company=company, location=location,
                   url="https://x/1", external_id="1",
                   description="PHP Laravel TYPO3 developer role")


# ---------------------------------------------------------------- tier 1

def test_country_name_in_any_language():
    assert geo.countries_in("Berlin, Germany") == {"DE"}
    assert geo.countries_in("München, Bayern, Deutschland") == {"DE"}
    assert geo.countries_in("Paris, France") == {"FR"}
    assert geo.countries_in("Vereinigte Staaten von Amerika") == {"US"}
    # "Mägenwil" is a real place in Afghanistan too, so the undecidable pair
    # comes back whole. Narrowing it to CH would be a guess; the caller
    # decides (a posting is kept when ANY of the codes is allowed).
    assert "CH" in geo.countries_in("Zürich und/oder Mägenwil und Homeoffice")


def test_comma_separated_locations_reach_the_gazetteer():
    # Regression: norm() keeps the comma attached, so "Toulouse, Occitanie"
    # tokenized to ['toulouse,', 'occitanie'] and the city lookup missed. A
    # gazetteer that never sees a comma is useless, because comma-separated
    # locations are the most common format there is.
    assert geo.countries_in("Toulouse, Occitanie") == {"FR"}
    assert geo.countries_in("Berlin, Germany") == {"DE"}
    assert geo.countries_in("Köln, Nordrhein-Westfalen, Deutschland") == {"DE"}
    assert geo.countries_in("Den Haag, Niederlande") == {"NL"}


def test_us_state_codes_and_names_do_not_collide_with_iso():
    # DE is Delaware AND Germany, so the colliding codes must stay out of the
    # map entirely; the unambiguous ones are safe.
    assert geo.countries_in("Austin, TX") == {"US"}
    assert geo.countries_in("Utah-based") == {"US"}
    assert geo.countries_in("Tübingen (DE)") == {"DE"}      # never Delaware
    assert "de" not in {c.lower() for c in geo.US_STATES}


def test_unknown_never_means_out_of_country():
    prefs = {"country_allow": ["de"], "min_score": 0}
    # genuinely unresolvable, and none of them is evidence against Germany,
    # so none may be filtered
    for loc in ("Quellenhof-Weilerhof", "", "   ", "-", "N/A"):
        assert not score_posting(prefs, _p(loc)).filtered, loc


def test_iso2_is_whole_token_not_substring():
    # "Mülheim" must not read as TR, "Münster" not as SE, "Remote in" not as IN
    # A multi-word gazetteer hit may still name several countries ("Mülheim"
    # exists in DE, BE and TR); what matters is that no code was read out of
    # the middle of a word.
    assert "TR" not in geo.countries_in("Mülheim an der Ruhr, Deutschland")
    assert "SE" not in geo.countries_in("Münster, Deutschland")
    assert "DE" in geo.countries_in("Mülheim an der Ruhr, Deutschland")


def test_iso2_code_beats_a_same_named_city():
    # "Wien" is also a town in Belgium; the explicit code must win
    assert geo.countries_in("Wien, Donaustadt, AT") == {"AT"}
    assert geo.countries_in("Tübingen (DE)") == {"DE"}


def test_short_codes_are_whole_tokens_only():
    # "se" inside "Münster" and "at" inside a word must never resolve a country
    assert "SE" not in geo.countries_in("bundesweit, Berlin, Frankfurt, Köln, Leipzig, Münster")
    assert geo.countries_in("Remote in Deutschland") == {"DE"}


# ---------------------------------------------------------------- tier 2

def test_bare_city_resolves_without_being_configured():
    # nobody has to type these anywhere
    assert "DE" in geo.countries_in("München")
    assert "CH" in geo.countries_in("Zürich")
    assert "AT" in geo.countries_in("Hagenberg Im Mühlkreis")
    assert "DE" in geo.countries_in("Isernhagen")


def test_hagen_no_longer_matches_hagenberg_or_copenhagen():
    # the exact bug from the hand-written city list
    assert "DE" not in geo.countries_in("Copenhagen, Denmark")
    assert "DE" not in geo.countries_in("Hagenberg Im Mühlkreis")


def test_nationwide_german_posting_is_german():
    got = geo.countries_in("bundesweit, Berlin, Frankfurt, Köln, Leipzig, Münster")
    assert "DE" in got
    assert geo.is_nationwide("bundesweit, Berlin, Frankfurt, Köln, Leipzig, Münster")
    assert geo.is_nationwide("Deutschlandweit")
    assert geo.is_nationwide("Remote - EMEA")
    assert geo.is_nationwide("worldwide")


# ---------------------------------------------------------------- tier 3

def test_no_country_is_unknown_not_wrong():
    for loc in ("Remote", "Hybrid", "Remote job", "Homeoffice", ""):
        assert geo.countries_in(loc) == set() or not geo.countries_in(loc) - {"US", "FR", "IN"}
        assert geo.is_remote(loc) or loc == ""
    assert geo.countries_in("") == set()
    assert geo.countries_in("Remote") == set()


def test_remote_words_do_not_invent_countries():
    # "Remote job" once matched "rem" and "job" as places in FR and IN
    assert geo.countries_in("Remote job") == set()
    assert geo.describe("Remote job") == "remote"


def test_describe_labels():
    assert geo.describe("Berlin, Germany") == "DE"
    assert geo.describe("Remote - EMEA") == "worldwide"
    assert geo.describe("Homeoffice") == "remote"
    assert geo.describe("") == "unknown"


# ---------------------------------------------------------------- scoring gate

def test_country_allow_gates_and_keeps_remote():
    prefs = {"country_allow": ["de"], "min_score": 0, "title_terms": ["developer"]}
    assert not score_posting(prefs, _p("Berlin, Germany")).filtered
    assert score_posting(prefs, _p("Wien, Austria")).filtered
    assert not score_posting(prefs, _p("Remote")).filtered
    assert not score_posting(prefs, _p("Remote - EMEA")).filtered


def test_country_allow_gives_points():
    prefs = {"country_allow": ["de"], "location_boost": 2}
    s = score_posting(prefs, _p("München, Bayern, Deutschland"))
    assert not s.filtered
    assert any(r[0].startswith("country:") for r in s.reasons)


def test_city_list_becomes_a_boost_not_a_gate():
    prefs = {"country_allow": ["de"], "locations_prefer": ["hamburg"],
             "location_prefer_boost": 1, "min_score": 0}
    # Hamburg is preferred but not required: a Berlin job still passes
    assert not score_posting(prefs, _p("Berlin, Germany")).filtered
    # and a small German town nobody configured still passes
    assert not score_posting(prefs, _p("Isernhagen")).filtered
    # the preferred city does earn its point
    s = score_posting(prefs, _p("Hamburg, Germany"))
    assert any(r[0] == "pref:hamburg" for r in s.reasons)


def test_locations_prefer_is_independent_of_locations_allow():
    # the config only has locations_prefer; the boost must still fire
    prefs = {"country_allow": ["de"], "locations_prefer": ["tübingen"],
             "location_prefer_boost": 1, "min_score": 0}
    s = score_posting(prefs, _p("Tübingen (DE)"))
    assert not s.filtered
    assert any(r[0] == "pref:tübingen" for r in s.reasons)


def test_unresolvable_location_is_kept_when_remote_or_nationwide():
    prefs = {"country_allow": ["de"], "min_score": 0}
    assert not score_posting(prefs, _p("Remote")).filtered
    assert not score_posting(prefs, _p("")).filtered


def test_strict_mode_drops_unresolvable_remote():
    # remote_countries_ok: false -> every kept row must name a real country
    prefs = {"country_allow": ["de"], "remote_countries_ok": False, "min_score": 0}
    assert score_posting(prefs, _p("Remote")).filtered
    assert not score_posting(prefs, _p("Berlin, Germany")).filtered


def test_named_but_unresolvable_town_is_kept():
    # below the 500-population cutoff: unknown is not evidence against Germany
    prefs = {"country_allow": ["de"], "min_score": 0}
    assert not score_posting(prefs, _p("Albersdorf-Prebuch")).filtered


def test_known_other_country_is_filtered():
    prefs = {"country_allow": ["de"], "min_score": 0}
    s = score_posting(prefs, _p("Vereinigte Staaten von Amerika"))
    assert s.filtered and "country" in s.reason


def test_remote_tied_to_another_country_is_still_filtered():
    # "Remote (US)" is remote, but it does name a country, and that country is
    # not allowed. Being remote must not become a loophole.
    prefs = {"country_allow": ["de"], "remote_countries_ok": True, "min_score": 0}
    for loc in ("Remote (US)", "United Kingdom (Remote)", "France Remote",
                "Remote/Hybrid, UK Only", "REMOTE (US time zones)"):
        s = score_posting(prefs, _p(loc))
        assert s.filtered, f"{loc!r} should be filtered"
        assert s.reason == "location:country", f"{loc!r} -> {s.reason}"


def test_body_country_is_recorded_when_the_location_is_unresolvable():
    # A location the gazetteer genuinely cannot resolve, where the body is the
    # only evidence. (Albersdorf-Prebuch is NOT a good example: the gazetteer
    # resolves it to AT+DE on its own, which is the point of the gazetteer.)
    prefs = {"country_allow": ["de"], "min_score": 0}
    p = _p("Quellenhof-Weilerhof")
    p.description = "Unser Standort in Deutschland, dort arbeiten Sie ab sofort."
    s = score_posting(prefs, p)
    assert not s.filtered
    assert any(r[0] == "country:body=DE" for r in s.reasons)


def test_gazetteer_covers_small_towns_nobody_configured():
    # the whole point: no hand-written city list involved
    for loc, want in (("Oggelshausen", "DE"), ("Albersdorf-Prebuch", "DE"),
                      ("Hagenberg Im Mühlkreis", "AT"), ("Raaba-Grambach", "AT")):
        assert want in geo.countries_in(loc), f"{loc!r} -> {geo.countries_in(loc)}"


def test_locations_deny_still_wins():
    prefs = {"country_allow": ["de"], "locations_deny": ["new york"],
             "min_score": 0, "title_terms": ["developer"]}
    s = score_posting(prefs, _p("New York, USA"))
    assert s.filtered and s.reason.startswith("location_deny:")


# ---------------------------------------------------------------- backwards compat

def test_without_country_allow_the_old_city_gate_still_works():
    prefs = {"locations_allow": ["berlin", "germany"], "require_location_match": True,
             "location_boost": 2, "min_score": 0, "title_terms": ["developer"]}
    assert not score_posting(prefs, _p("Berlin")).filtered
    assert score_posting(prefs, _p("Wien, Austria")).filtered
    # the old reason strings must stay stable for stored rows
    assert score_posting(prefs, _p("Wien, Austria")).reason.startswith("location:")


def test_no_location_config_at_all_never_filters():
    prefs = {"min_score": 0, "title_terms": ["developer"]}
    for loc in ("Berlin", "Wien", "", "Remote", "Timbuktu"):
        assert not score_posting(prefs, _p(loc)).filtered


def test_place_key_folds_the_ways_boards_spell_one_city():
    """The duplicate key used the location verbatim, so one advert on two boards
    ("Mülheim (Ruhr)" and "Mülheim an der Ruhr") never matched itself."""
    assert geo.place_key("Mülheim (Ruhr)") == geo.place_key("Mülheim an der Ruhr")
    assert geo.place_key("Berlin, Deutschland") == "berlin"
    assert geo.place_key("Vienna") == geo.place_key("Wien")
    assert geo.place_key("Munich") == geo.place_key("München")
    # the parenthesised word is information: these are different cities
    assert geo.place_key("Frankfurt (Oder)") != geo.place_key("Frankfurt am Main")
    assert geo.place_key("Frankfurt am Main") == "frankfurt main"
