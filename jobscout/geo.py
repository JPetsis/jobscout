"""Country resolution for a free-text job location, fully offline.

The problem this replaces: a country is not a term, so it was being emulated
with a hand-written list of city names. Germany has ~11,000 municipalities, so
that list can never be complete — and it was already wrong in both directions
("hagen" matched "Hagenberg" in Austria and "Copenhagen", while "bundesweit,
Berlin, Frankfurt, Köln, Leipzig, Münster" matched nothing).

Resolution is a three-tier cascade, cheapest first. Every tier is static data;
nothing here touches the network.

  tier 1  explicit country name in any language the sources actually emit
          ("Germany", "Deutschland", "France", "Vereinigte Staaten von
          Amerika", "Schweiz", "Frankreich", ...), or a Bundesland
          ("Bayern", "Sachsen", "Hesse", "North Rhine-Westphalia", ...), or
          the nationwide markers ("bundesweit", "germany-wide").
  tier 2  an offline gazetteer of 234,908 cities (pop >= 500) shipped inside
          geonamescache as JSON. A bare "München", "Konstanz", "Oggelshausen"
          or "Korneuburg" resolves here without anyone typing it in.
  tier 3  no country and no city — "Remote", "Hybrid", "Remote - EMEA", "".
          These are UNKNOWN, and unknown is a legitimate answer that the
          caller gets to treat as rank-without-filter.

Two details that decide whether this works at all, both found the hard way:

  * geonamescache stores UPPERCASE ISO-3166 codes ("DE"), so any lookup keyed
    on normalised (lowercased) text must uppercase the code. Skipping this
    resolves 4 of 2,121 real locations instead of 1,458.
  * 775 of the real location strings match MORE THAN ONE country, because
    city names are not unique: "Berlin" exists in DE, HN, RU, SV, US and ZA.
    A gazetteer hit therefore cannot decide on its own — the tiers intersect,
    and a country-name signal outranks a bare city hit. That intersection is
    what keeps "Berlin" German while "Vereinigte Staaten von Amerika" stays US.

A hamlet below the 500-population cutoff (Albersdorf-Prebuch) may stay
unresolved. That is the intended behaviour: the honest answer is "unknown",
never a guess.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .textutil import norm

# --------------------------------------------------------------------------
# tier 1: country and subdivision names, in the languages these sources emit
# --------------------------------------------------------------------------
# ISO-3166 alpha-2 -> surface forms. Kept deliberately compact: forms come
# from observed location strings, not from a full CLDR dump.
#
# `norm()` does NOT strip diacritics, so "Österreich" and "osterreich" are
# different strings and BOTH forms are needed. Writing only the ASCII form
# silently loses every German-language location — which is most of them.
COUNTRY_WORDS: dict[str, list[str]] = {
    "DE": ["germany", "deutschland", "bundesrepublik deutschland", "brd", "ger",
           "allemagne", "germania", "deutschland (de)", "germany (de)"],
    "AT": ["austria", "osterreich", "oesterreich", "österreich", "aut",
           "autriche", "austria (at)"],
    "CH": ["switzerland", "schweiz", "suisse", "svizzera", "che", "confoederatio",
           "deutschschweiz", "schweizerische"],
    "FR": ["france", "frankreich", "fra", "frankreich (fr)"],
    "US": ["usa", "united states", "united states of america", "vereinigte staaten",
           "vereinigte staaten von amerika", "vereinigtes staaten", "america",
           "vereinigte staaten von nordamerika", "u.s.a."],
    "GB": ["united kingdom", "great britain", "england", "scotland", "wales",
           "britain", "vereinigtes konigreich", "vereinigtes königreich", "eng",
           "gbr", "uk", "großbritannien", "grossbritannien"],
    "IT": ["italy", "italien", "italia", "ita", "italia (it)"],
    "ES": ["spain", "spanien", "espana", "españa", "esp", "españa (es)"],
    "NL": ["netherlands", "niederlande", "holland", "nld", "nederland",
           "niederlande (nl)"],
    "BE": ["belgium", "belgien", "belgique", "belgique", "bel", "belgien (be)"],
    "PL": ["poland", "polen", "polska", "pol"],
    "PT": ["portugal", "prt"],
    "IN": ["india", "indien", "ind"],
    "BR": ["brazil", "brasilien", "brasil", "bra", "brasil (br)"],
    "CA": ["canada", "kanada", "can"],
    "MX": ["mexico", "mexiko", "mex", "méxico"],
    "IE": ["ireland", "irland", "irl"],
    "AU": ["australia", "australien", "aus"],
    "ZA": ["south africa", "sudafrika", "südafrika", "zaf"],
    "IL": ["israel", "isr"],
    "TR": ["turkey", "turkei", "türkei", "tur"],
    "SE": ["sweden", "schweden", "swe"],
    "NO": ["norway", "norwegen", "nor", "norge"],
    "DK": ["denmark", "danemark", "dnk", "dänemark"],
    "FI": ["finland", "finnland", "fin", "suomi"],
    "CZ": ["czechia", "czech republic", "tschechien", "cze", "tschechische"],
    "HU": ["hungary", "ungarn", "hun", "magyarország"],
    "RO": ["romania", "romanien", "rumänien", "rou"],
    "BG": ["bulgaria", "bulgarien", "bgr"],
    "HR": ["croatia", "kroatien", "hrv", "hrvatska"],
    "SI": ["slovenia", "slowenien", "svn", "slovenija"],
    "SK": ["slovakia", "slowakei", "svk"],
    "UA": ["ukraine", "ukr"],
    "RS": ["serbia", "serbien", "srb", "srbija"],
    "LT": ["lithuania", "litauen", "ltu", "lietuva"],
    "LV": ["latvia", "lettland", "lva"],
    "EE": ["estonia", "estland", "est", "eesti"],
    "IS": ["iceland", "island", "isl"],
    "MT": ["malta", "mlt"],
    "CY": ["cyprus", "zypern", "cyp"],
    "LU": ["luxembourg", "luxemburg", "lux"],
    "MA": ["morocco", "marokko", "mar"],
    "AR": ["argentina", "argentinien", "arg"],
    "CL": ["chile", "chile"],
    "CO": ["colombia", "kolumbien"],
    "PE": ["peru", "peru", "per"],
    "UY": ["uruguay", "uruguay"],
    "JP": ["japan", "jpn", "japan (jp)"],
    "CN": ["china", "chn", "volksrepublik china", "prc"],
    "KR": ["south korea", "südkorea", "korea"],
    "TW": ["taiwan"],
    "HK": ["hong kong"],
    "SG": ["singapore", "sgp"],
    "MY": ["malaysia", "malaysia (my)"],
    "ID": ["indonesia", "indonesien"],
    "TH": ["thailand", "th"],
    "VN": ["vietnam", "viet nam"],
    "PH": ["philippines", "philippinen"],
    "PK": ["pakistan"],
    "NG": ["nigeria"],
    "KE": ["kenia", "kenya"],
    "GH": ["ghana"],
    "TN": ["tunesien", "tunisia"],
    "DZ": ["algerien", "algeria"],
    "EG": ["ägypten", "egypt"],
    "ET": ["äthiopien", "ethiopia"],
    "NZ": ["new zealand", "neuseeland", "nzl"],
}

# Austria's and Switzerland's own federal states. Without these,
# "Steiermark, Wien, Voralberg, Kärnten, Niederösterreich, Burgenland" — a
# purely Austrian location — resolves to nothing, because those names are not
# a German Bundesland and the country name is never stated.
AT_STATES = ["wien", "vienna", "niederösterreich", "niederoesterreich",
             "oberösterreich", "oberoesterreich", "salzburg", "steiermark",
             "stmk", "burgenland", "kärnten", "kaernten", "carinthia", "tyrol",
             "tirol", "vorarlberg", "osttirol", "styria", "lower austria",
             "upper austria", "burgenland (at)"]
CH_STATES = ["zürich", "zurich", "zuerich", "genf", "genève", "ginebra",
             "basel", "baselland", "bern", "bernau", "freiburg", "fribourg",
             "glarus", "graubünden", "graubuenden", "grisons", "jura",
             "luzern", "lucerne", "neuenburg", "neuchâtel", "neuchatel",
             "nidwalden", "obwalden", "schaffhausen", "schwyz", "solothurn",
             "st. gallen", "sankt gallen", "tessin", "ticino", "thurgau",
             "uri", "waadt", "vaud", "wallis", "valais", "Appenzell",
             "aargau", "aargau (ch)"]

# US state codes appear constantly in ATS location fields ("Austin, TX",
# "Utah-based") and were the biggest remaining source of US postings that no
# country name or gazetteer entry would catch.
#
# This map is DELIBERATELY INCOMPLETE. These codes collide with real ISO-3166
# codes and must never resolve: DE = Delaware AND Germany, CA = California AND
# Canada, IN = Indiana AND India, MA = Massachusetts AND Morocco,
# AR = Arkansas AND Argentina, CO = Colorado AND Colombia,
# ID = Idaho AND Indonesia, LA = Louisiana AND Latvia, MT = Montana AND Malta.
# Dropping them costs a few US postings and removes any chance of reading
# "Tübingen (DE)" as Delaware. Word-like codes (IN, OR, AS, IT) go too.
US_STATES = {
    "ak": "US", "az": "US", "ct": "US", "fl": "US", "ga": "US", "hi": "US",
    "ia": "US", "ks": "US", "ky": "US", "md": "US", "me": "US", "mi": "US",
    "mn": "US", "mo": "US", "ms": "US", "nc": "US", "nd": "US", "ne": "US",
    "nh": "US", "nj": "US", "nm": "US", "nv": "US", "ny": "US", "oh": "US",
    "ok": "US", "pa": "US", "ri": "US", "sc": "US", "sd": "US", "tn": "US",
    "tx": "US", "ut": "US", "va": "US", "vt": "US", "wa": "US", "wi": "US",
    "wv": "US", "wy": "US",
}

# Germany's 16 Bundeslaender, EN and DE. A closed set, so a match is strong
# evidence: no other country has a subdivision called "Bayern" or "Sachsen".
DE_STATES = [
    "bayern", "sachsen", "hessen", "brandenburg", "bremen", "hamburg",
    "niedersachsen", "nordrhein-westfalen", "rheinland-pfalz", "saarland",
    "sachsen-anhalt", "schleswig-holstein", "thuringen", "thueringen",
    "baden-wurttemberg", "baden-wuerttemberg", "wurttemberg", "baden",
    "bavaria", "saxony", "saxony-anhalt", "hesse", "north rhine-westphalia",
    "rhineland-palatinate", "schleswig holstein", "mecklenburg-vorpommern",
    "mecklenburg western pomerania", "mecklenburg",
]

# Nationwide / remote markers. These are not countries, so they resolve to
# nothing here; they are handled by the caller as "no geographic restriction".
NATIONWIDE = re.compile(
    r"(?<![a-z])(bundesweit|deutschlandweit|bundesweit|Deutschlandweit|"
    r"germany-wide|germanywide|nationwide|worldwide|world-wide|global|"
    r"anywhere|emsa|emea|eu-?wide|apac)(?![a-z])", re.I)

REMOTE = re.compile(
    r"(?<![a-z])(remote|home-?office|homeoffice|telearbeit|ortsunabhangig|"
    r"ortsunabhängig|mobiles|arbeitsort:(?:flexibel|home)|hybrid)(?![a-z])", re.I)


# Short, ambiguous codes must be matched as whole TOKENS, never as substrings.
# "at" inside "austria" or "in" inside "Remote in Deutschland" produces false
# countries; "se" inside "Münster" produced FR and SE for a German string.
#: How boards translate the same city. Small on purpose: only the names that
#: actually differ between German and English postings.
_CITY_ALIAS = {
    "munich": "münchen", "vienna": "wien", "zurich": "zürich",
    "cologne": "köln", "nuremberg": "nürnberg", "hanover": "hannover",
    "brunswick": "braunschweig", "mayence": "mainz", "vienna": "wien",
}

#: Connectors and country words that say nothing about WHICH city it is.
_PLACE_FILLER = frozenset({"an", "der", "am", "im", "in", "bei"})
_PLACE_COUNTRY = frozenset({"deutschland", "germany", "österreich", "austria",
                            "schweiz", "switzerland", "de", "at", "ch"})


def place_key(location: str) -> str:
    """One city, however the board spells it.

    "Mülheim (Ruhr)" and "Mülheim an der Ruhr" are the same place, and treating
    them as different is how the same ALDI SUD role arrived twice with no
    duplicate marking. The parenthesised word is KEPT, because "Frankfurt (Oder)"
    and "Frankfurt am Main" are different cities; only connectors and trailing
    country names are dropped.
    """
    words = [w for w in re.split(r"[^0-9a-zäöüß]+", norm(location)) if w]
    words = [_CITY_ALIAS.get(w, w) for w in words]
    words = [w for w in words if w not in _PLACE_FILLER]
    while words and words[-1] in _PLACE_COUNTRY:
        words.pop()
    return " ".join(words)


def _word_re(phrase: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", re.I)


_COUNTRY_RES: dict[str, re.Pattern] = {}


def _country_named(text: str) -> set[str]:
    """ISO codes whose name appears in `text` (already normalised)."""
    out: set[str] = set()
    for code, words in COUNTRY_WORDS.items():
        pat = _COUNTRY_RES.get(code)
        if pat is None:
            # every form is matched as a whole token/phrase
            alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
            pat = re.compile(rf"(?<![a-z])(?:{alts})(?![a-z])")
            _COUNTRY_RES[code] = pat
        if pat.search(text):
            out.add(code)
    return out


# An explicit ISO-3166 alpha-2 code is the strongest signal a location string
# can carry ("Tübingen (DE)", "Wien, Donaustadt, AT", "Remote Job (DE)"). It
# must win over any gazetteer coincidence, because "Wien" is a town in Belgium
# too and the code says Austria.
_ISO2_RE = re.compile(r"(?<![a-z0-9])([a-z]{2})(?:[),;]|$)(?![a-z0-9])")
_ISO2_CODES = frozenset(COUNTRY_WORDS) | frozenset(
    code for code in ("DK", "NO", "FI", "EE", "LV", "LT", "IS", "LU",
                      "MT", "CY", "CL", "CO", "PE", "VE", "EC", "UY",
                      "KZ", "BY", "GE", "AM", "AZ", "UZ", "MN", "KH",
                      "MM", "TH", "VN", "PH", "ID", "MY", "TW", "HK",
                      "ZA", "EG", "NG", "KE", "GH", "TN", "DZ", "ET"))


# Job-board filler that is never a place name. Stripped before the gazetteer
# lookup when the string is otherwise only remote wording, so that
# "Remote job" cannot match "rem"/"job" while "Zürich ... Homeoffice" still
# matches "Zürich".
GENERIC_WORDS = frozenset("""
job jobs office offices based location locations place positions position
rem wfh mft all bundesweit bundesland nationwide worldwide global anywhere
hybrid remote homeoffice home office telearbeit ortsunabhängig ortsunabhangig
freelance contractor m/w/d wmd m w d f x gesalllgemein divers
""".split())


def _is_generic_word(word: str) -> bool:
    """True for job-board filler that is not a place ("job", "office", "rem")."""
    if not word or len(word) < 2:
        return True
    return word in GENERIC_WORDS


# Separators that survive `norm()`. Without this, "Toulouse, Occitanie"
# tokenizes to ['toulouse,', 'occitanie'] and the gazetteer lookup misses the
# city entirely — and comma-separated locations are the most common format.
_SPLIT_RE = re.compile(r"[,;/()\[\]{}|·&+\-–—\"'`]+")


def _tokenize(text: str) -> list[str]:
    """Split a normalised location into clean lookup tokens."""
    out: list[str] = []
    for raw in _SPLIT_RE.split(text):
        for piece in raw.split():
            if piece:
                out.append(piece)
    return out


def _iso2_codes(text: str) -> set[str]:
    """Explicit two-letter country codes appearing as standalone tokens."""
    out: set[str] = set()
    for raw in _ISO2_RE.findall(text):
        code = raw.upper()
        if code in _ISO2_CODES:
            out.add(code)
    return out


@lru_cache(maxsize=1)
def _state_re() -> re.Pattern:
    alts = "|".join(re.escape(s) for s in sorted(DE_STATES, key=len, reverse=True))
    return re.compile(rf"(?<![a-z])(?:{alts})(?![a-z])", re.I)


# Austria/Switzerland subdivisions resolve to their own country, NOT to the
# gazetteer: "Wien" alone is ambiguous, but "Steiermark" or "Niederösterreich"
# are unique to Austria and need no corroboration.
_STATE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("DE", _state_re()),
) + tuple(
    (code, re.compile(rf"(?<![a-z])(?:{alts})(?![a-z])", re.I))
    for code, states in (("AT", AT_STATES), ("CH", CH_STATES))
    for alts in ("|".join(re.escape(s) for s in sorted(states, key=len, reverse=True)),)
)

# US state NAMES. Needed because ATS boards write "Utah-based", "New York, NY"
# and "San Francisco, California" as often as they write codes, and a bare
# "-based" suffix means the location is US. Names are unambiguous, unlike the
# two-letter codes, so the whole set is safe.
_US_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "ohio", "oklahoma", "oregon",
    "pennsylvania", "tennessee", "texas", "utah", "vermont", "wisconsin",
    "wyoming",
]
US_STATE_NAME_RE = re.compile(
    r"(?<![a-z])(?:" + "|".join(sorted(_US_STATE_NAMES, key=len, reverse=True))
    + r")(?![a-z])", re.I)


# --------------------------------------------------------------------------
# tier 2: the offline gazetteer
# --------------------------------------------------------------------------
# Alternatenames is noisy: real entries up to 4 words and 40 chars, plus long
# junk strings. Anything longer is a bug in the data, not a place name.
_MAX_NAME_WORDS = 4
_MAX_NAME_CHARS = 40
# Below this length a gazetteer entry is noise, not a place: "N/A" resolved to
# NO because Norway has a town called "N", and 2-letter tokens collide with
# ISO-3166 codes.
_MIN_CITY_LEN = 3


@lru_cache(maxsize=1)
def _city_index() -> dict[str, set[str]]:
    """normalised city name (EN + alternatenames) -> set of ISO2 codes.

    Absent geonamescache = no tier 2, and resolution degrades to tiers 1+3
    rather than raising. Scoring must never depend on an optional dataset.
    """
    try:
        from geonamescache import GeonamesCache
    except ImportError:                      # pragma: no cover - optional dep
        return {}
    try:
        cities = GeonamesCache(min_city_population=500).get_cities()
    except Exception:                        # pragma: no cover - corrupt data
        return {}

    index: dict[str, set[str]] = {}
    for city in cities.values():
        # UPPERCASE codes: see module docstring, this is load-bearing.
        code = (city.get("countrycode") or "").upper()
        if not code:
            continue
        names = [city.get("name") or ""]
        names.extend(city.get("alternatenames") or [])
        for raw in names:
            if not raw:
                continue
            key = norm(raw)
            if not key or len(key) > _MAX_NAME_CHARS:
                continue
            # A one- or two-character gazetteer entry is a false-positive
            # generator, not a place worth matching: "N/A" resolved to NO
            # because Norway has a town literally called "N", and short
            # tokens collide with ISO-3166 codes.
            if len(key) < _MIN_CITY_LEN:
                continue
            if len(key.split()) > _MAX_NAME_WORDS:
                continue
            index.setdefault(key, set()).add(code)
    return index


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def countries_in(location: str) -> set[str]:
    """ISO-3166 codes for a location string; empty set means "unknown".

    Precedence, strongest evidence first:

      1. a country named in the string, intersected with the countries its
         gazetteer cities belong to. An explicit "Deutschland" outranks any
         city-name coincidence.
      2. a bare gazetteer city ("Berlin" -> DE/HN/RU/SV/US/ZA, "Zürich" ->
         CH). When several countries share the name, the result is the whole
         set: the caller decides, and an undecidable name must NOT be silently
         narrowed to the one country the user cares about.
      3. nothing -> empty set, i.e. unknown.
    """
    if not location or not location.strip():
        return set()
    text = norm(location)
    if not text:
        return set()

    named = _country_named(text) | _iso2_codes(text)
    for code, pat in _STATE_PATTERNS:
        if pat.search(text):
            named.add(code)
    # US state code: only when it stands alone after a comma or as a suffix
    # ("Austin, TX", "Utah-based"), never as a bare word.
    for m in re.finditer(r"(?<![a-z0-9])([a-z]{2})(?![a-z0-9])", text):
        code = US_STATES.get(m.group(1))
        if code:
            named.add(code)
    if US_STATE_NAME_RE.search(text):
        named.add("US")

    cities = _city_index()
    gazetteer: set[str] = set()
    if cities:
        # Words like "Remote" / "worldwide" say where the job is NOT, so they
        # must suppress gazetteer noise hits ("Remote job" matches "rem" and
        # "job" as places in France and India). They must NOT suppress real
        # place names, because "Zürich und/oder Mägenwil und Homeoffice" and
        # "bundesweit, Berlin, Frankfurt, Köln, Leipzig, Münster" both carry
        # genuine geography alongside remote wording. So tier 2 runs on the
        # parts that are NOT remote/nationwide wording, and only when at least
        # one such part remains.
        has_country = bool(named)
        if has_country or not REMOTE.search(text):
            words = _tokenize(text)
        else:
            # strip the generic words, then see whether anything geographic
            # is left: this is the only way "Zürich ... Homeoffice" survives
            words = [w for w in _tokenize(text) if not _is_generic_word(w)]
        if has_country or not REMOTE.search(text) or words:
            for i in range(len(words)):
                for span in range(1, _MAX_NAME_WORDS + 1):
                    hit = cities.get(" ".join(words[i:i + span]))
                    if hit:
                        gazetteer.update(hit)

    if not named:
        # tier 2: the city alone is the only evidence there is
        return gazetteer
    if not gazetteer:
        return named
    # Intersection, not union: a country named in the string must be among
    # the countries its cities belong to. This is what stops a bare "Berlin"
    # (DE, HN, RU, SV, US, ZA) from being read as six countries at once, and
    # what keeps "Vereinigte Staaten von Amerika" resolving to US only.
    inter = named & gazetteer
    return inter or named


def is_nationwide(location: str) -> bool:
    """True for "bundesweit", "Remote - EMEA", "worldwide" — no single country."""
    return bool(location) and bool(NATIONWIDE.search(norm(location)))


def is_remote(location: str) -> bool:
    return bool(location) and bool(REMOTE.search(norm(location)))


def describe(location: str) -> str:
    """Short human label used in the scoring `why:` line."""
    codes = sorted(countries_in(location))
    if codes:
        return "/".join(codes)
    if is_nationwide(location):
        return "worldwide"
    if is_remote(location):
        return "remote"
    return "unknown"
