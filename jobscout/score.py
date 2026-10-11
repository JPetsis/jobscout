"""Deterministic, transparent scoring of a posting against the preferences.

Rule families (lists and weights live in [preferences] of the config):

  title        title_terms hit the TITLE            -> +title_boost each, capped at title_cap
               title_deny filters on the TITLE
  stack        stack_terms hit TITLE or DESCRIPTION -> +stack_boost each, capped at stack_cap
               stack_min_hits > 0 filters postings with fewer stack hits
  want         stack_preferred (a term -> 1..3 interest map) hit TITLE or DESCRIPTION
               -> +that weight each, capped at stack_preferred_cap (0/absent =
               UNCAPPED, so weights mean what they say), scored BEFORE stack_terms
               and on its own budget. This is how "PHP matters more than Python"
               is expressed: weight, not list order. Order-independent. A term on
               both this and stack_terms is paid for ONCE, at this weight.
  description  description_terms hit the DESCRIPTION -> +description_weight each, capped
               description_deny filters on the DESCRIPTION
  keywords     keywords hit title/company/location/description -> +keyword_weight each, capped
               exclude_terms filter anywhere
  location     country_allow (ISO-3166, offline resolution) -> gate: a posting
               whose country is resolvable and not allowed is filtered;
               locations with no country at all (remote, nationwide) are kept.
               locations_allow is only a RANKING boost when country_allow is
               set, and stays a gate when it is not (backwards compatible).
               locations_deny still filters first; remote -> +remote_bonus
               See jobscout/geo.py for the resolution cascade.
  freshness    posted date older than max_age_days -> filtered (only when a date is known)
  salary       known annual pay below min_salary -> filtered; postings without a parsed
               salary are kept, and no FX conversion happens (numbers compare as-is)

Term matching has two modes:
  * scoring lists (title_terms, stack_terms, description_terms, keywords,
    locations_allow): boundary-friendly. A term hits as a whole word or as part
    of a compound, so "entwickler" hits "Softwareentwickler", but "git" does
    not hit "digital".
  * filter lists (title_deny, description_deny, exclude_terms, locations_deny):
    whole words only (plus multi-word phrases), so "sales" does not hit
    "Salesforce". False drops are the expensive mistake; misses just show up in
    the digest as noise.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import geo
from .models import Posting
from .textutil import date_part, norm

_TERM_RES: dict[tuple[str, bool], re.Pattern] = {}


@dataclass
class Score:
    score: float = 0.0
    reasons: list = field(default_factory=list)   # [[label, points], ...]
    filtered: bool = False
    reason: str = ""


def prefs_hash(prefs: dict) -> str:
    """Stable fingerprint of the preferences block (used for change detection)."""
    blob = json.dumps(prefs, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _num(prefs: dict, key: str, default: float) -> float:
    try:
        return float(prefs.get(key, default))
    except (TypeError, ValueError):
        return default


def _term_re(term: str, strict: bool) -> re.Pattern:
    key = (term, strict)
    pat = _TERM_RES.get(key)
    if pat is None:
        esc = re.escape(term)
        if strict:
            pat = re.compile(rf"(?<![0-9a-z]){esc}(?![0-9a-z])")
        else:
            pat = re.compile(rf"(?:(?<![0-9a-z]){esc}|{esc}(?![0-9a-z]))")
        _TERM_RES[key] = pat
    return pat


def _hit(text: str, term: str, strict: bool = False) -> bool:
    """Match an already-normalized term against already-normalized text."""
    tn = norm(term)
    return bool(tn) and _term_re(tn, strict).search(text) is not None


def _first_hit(terms, text: str, strict: bool = True) -> str | None:
    for term in terms:
        if _hit(text, term, strict=strict):
            return term
    return None


def _boost(s: Score, terms, text: str, label: str, weight: float, cap: float) -> float:
    """Score distinct term hits, each worth ``weight``, in total capped at ``cap``."""
    used = 0.0
    for term in terms:
        if not _hit(text, term):
            continue
        add = min(weight, cap - used)
        if add <= 0:
            break
        used += add
        s.score += add
        s.reasons.append([f"{label}:{term}", add])
    return used


def _count_hits(terms, text: str) -> int:
    return sum(1 for term in terms if _hit(text, term))


def _stack_preferred(prefs: dict) -> dict:
    """Normalise the `stack_preferred` map to {term: float weight}.

    Accepts the YAML mapping form (term -> 1..3). Anything that is not a
    number is dropped rather than crashing a scan, because preferences are
    hand-edited.
    """
    raw = prefs.get("stack_preferred") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for term, weight in raw.items():
        t = norm(str(term))
        if not t:
            continue
        try:
            out[t] = float(weight)
        except (TypeError, ValueError):
            continue
    return out


def _boost_weighted(s: Score, weights: dict, text: str, label: str,
                    cap: float | None = None) -> float:
    """Score terms by their OWN weight (the interest scale).

    Properties:

    * It does NOT consume `stack_cap`. A wanted technology must not have its
      points eaten by table stakes that happen to be listed first.
    * It is order-independent — terms are walked heaviest-first for a stable
      `reasons` list, so a PHP role and a Laravel role score the same total
      regardless of YAML order.
    * `cap=None` means UNCAPPED (Proposal B): weights mean what the config
      says they mean, so rescaling 1-3 -> 1-4 is a real change. A cap makes
      scale size nearly meaningless, because postings that mention a lot of
      anything all pin to the same ceiling. `cap<=0` is treated as uncapped.

    De-duplication against `stack_terms` is the CALLER's job (it owns both
    lists), so this stays a pure weighted boost.
    """
    limit = float("inf") if cap is None or cap <= 0 else float(cap)
    used = 0.0
    for term, weight in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        if weight <= 0 or not _hit(text, term):
            continue
        add = min(weight, limit - used)
        if add <= 0:
            break
        used += add
        s.score += add
        s.reasons.append([f"{label}:{term}", add])
    return used


def _body_country(prefs: dict, p: Posting, country_allow: set[str]) -> str:
    """Last resort: an ISO code spelled out in the description.

    A hamlet like "Albersdorf-Prebuch" is below the gazetteer cutoff, so the
    location alone cannot be resolved. Many such postings still say "DE" or
    "Germany" in the body, which is a far better signal than dropping them.
    """
    if not prefs.get("country_body_fallback", True):
        return ""
    desc = norm(p.description or "")
    for code in sorted(country_allow):
        if re.search(rf"(?<![a-z]){code.lower()}(?![a-z])", desc) or \
           any(w in desc for w in geo.COUNTRY_WORDS.get(code, [])):
            return code
    return ""


def score_posting(prefs: dict, p: Posting, today: date | None = None) -> Score:
    s = Score()
    today = today or date.today()
    title = norm(p.title)
    desc = norm(p.description)
    loc = norm(p.location)
    text = " ".join(x for x in (title, norm(p.company), loc, desc) if x)

    # hard filters first (cheap reject, clear reason); whole-word matching
    term = _first_hit(prefs.get("exclude_terms", []), text)
    if term:
        s.filtered, s.reason = True, f"excluded:{term}"
        return s
    term = _first_hit(prefs.get("title_deny", []), title)
    if term:
        s.filtered, s.reason = True, f"title_deny:{term}"
        return s
    term = _first_hit(prefs.get("description_deny", []), desc)
    if term:
        s.filtered, s.reason = True, f"description_deny:{term}"
        return s

    max_age = _num(prefs, "max_age_days", 0)
    d = date_part(p.posted_at)
    if max_age and d:
        try:
            if date.fromisoformat(d[:10]) < today - timedelta(days=max_age):
                s.filtered, s.reason = True, f"stale:{d}"
                return s
        except ValueError:
            pass

    min_salary = _num(prefs, "min_salary", 0)
    if min_salary:
        annual = getattr(p, "salary_annual_max", None)
        if annual and annual < min_salary:
            s.filtered, s.reason = True, f"salary:below-{min_salary:g}"
            return s

    stack_text = f"{title} {desc}"
    stack_terms = prefs.get("stack_terms", [])
    stack_hits = [t for t in stack_terms if _hit(stack_text, t)]
    # Boilerplate-inflation guard: company blurbs name every technology they can,
    # so stack points only count when the title is role-relevant. Disable with
    # stack_needs_title_match: false in the config.
    title_relevant = bool(_count_hits(prefs.get("title_terms", []), title))
    needs_title = prefs.get("stack_needs_title_match", True)
    if not needs_title:
        title_relevant = True          # guard off: every title counts as relevant
    elif not title_relevant:
        stack_hits = []
    min_hits = int(_num(prefs, "stack_min_hits", 0))
    if min_hits > 0 and len(stack_hits) < min_hits:
        s.filtered, s.reason = True, f"stack_min_hits:{len(stack_hits)}<{min_hits}"
        return s

    _boost(s, prefs.get("title_terms", []), title, "title",
           _num(prefs, "title_boost", 3), _num(prefs, "title_cap", 6))
    # The interest scale (`stack_preferred`, term -> 1..3) is scored FIRST and
    # on its own budget, so a posting built around what the user actually wants
    # outranks an equally titled one full of table stakes. It is gated by the
    # same title-relevance rule as stack_terms.
    #
    # SINGLE SOURCE OF TRUTH: when a term appears in BOTH `stack_preferred` and
    # `stack_terms` (the natural state after adding the scale to a config that
    # already had one), it must be paid for ONCE. The weighted value wins and
    # the term is dropped from the flat `stack_terms` list, so `php` is +3 and
    # never +3+2 for the same technology. `stack_terms` keeps its original
    # behaviour untouched when no scale is configured.
    preferred = _stack_preferred(prefs)
    if preferred:
        # Drop every scaled term from the flat list; it is already paid for
        # at its own weight below. `preferred` keys are normalised by
        # _stack_preferred(), so the comparison is apples to apples.
        flat_terms = [t for t in stack_terms if norm(t) not in preferred]
        if title_relevant:
            # 0 or absent = UNCAPPED (Proposal B). With a cap, rescaling the
            # scale is a no-op, because every mention-heavy posting pins to the
            # same ceiling and the weights stop discriminating.
            _boost_weighted(s, preferred, stack_text, "want",
                            _num(prefs, "stack_preferred_cap", 0) or None)
    else:
        flat_terms = stack_terms
    _boost(s, flat_terms if title_relevant else [], stack_text, "stack",
           _num(prefs, "stack_boost", 2), _num(prefs, "stack_cap", 8))
    _boost(s, prefs.get("description_terms", []), desc, "desc",
           _num(prefs, "description_weight", 1), _num(prefs, "description_cap", 6))
    _boost(s, prefs.get("keywords", []), text, "kw",
           _num(prefs, "keyword_weight", 1), _num(prefs, "keyword_cap", 8))

    # `locations_prefer` (preferred cities) is only a ranking boost; the legacy
    # `locations_allow` keeps working as a gate when `country_allow` is absent.
    prefer = [t for t in prefs.get("locations_prefer", []) if norm(t)]
    allow = [t for t in prefs.get("locations_allow", []) if norm(t)]
    deny = [t for t in prefs.get("locations_deny", []) if norm(t)]
    loc_deny = next((t for t in deny if _hit(loc, t, strict=True)), None)
    if loc_deny:
        s.filtered, s.reason = True, f"location_deny:{loc_deny}"
        return s

    # `country_allow` is the country-level gate and supersedes the city list
    # for FILTERING. The city list is demoted to a ranking boost so a city is
    # rewarded without being required — a country is not a term, so emulating
    # one with city names could never be complete (Germany has ~11,000
    # municipalities) and was already wrong in both directions.
    country_allow = {c.strip().upper() for c in prefs.get("country_allow", []) if c.strip()}
    remote_ok = bool(prefs.get("remote_countries_ok", True))
    countries = geo.countries_in(p.location or "")
    nationwide = geo.is_nationwide(p.location or "")
    remote = geo.is_remote(p.location or "")

    if country_allow:
        # A resolvable country must be allowed. A bare city name that is
        # genuinely ambiguous ("Berlin" is also in HN, RU, SV, US, ZA) passes
        # when ANY of its countries is allowed, because narrowing it would be
        # a guess.
        if countries & country_allow:
            boost = _num(prefs, "location_boost", 2)
            s.score += boost
            s.reasons.append([f"country:{geo.describe(p.location or '')}", boost])
        elif not countries:
            # No country could be resolved at all. That is an ABSENCE of
            # evidence, not evidence against the target country, so the posting
            # is kept and ranked: remote and nationwide postings obviously, and
            # also named places the gazetteer does not carry (a hamlet below
            # its 500-population cutoff). Only a location that positively
            # resolves to a country you did not allow is filtered.
            # `remote_countries_ok: false` is the strict variant: then an
            # unresolvable REMOTE posting is dropped too, for someone who wants
            # an actual country on every row.
            if remote and not remote_ok and loc:
                s.filtered = True
                s.reason = "location:unknown"
                return s
            body = _body_country(prefs, p, country_allow)
            if body:
                s.reasons.append([f"country:body={body}", 0])
        else:
            s.filtered = True
            s.reason = "location:country"
            return s
    else:
        loc_match = next((t for t in allow if _hit(loc, t)), None)
        if loc_match:
            boost = _num(prefs, "location_boost", 2)
            s.score += boost
            s.reasons.append([f"loc:{loc_match}", boost])
        elif prefs.get("require_location_match") and allow:
            s.filtered, s.reason = True, ("location:no-match" if loc else "location:unknown")
            return s

    # Preferred-city boost: `locations_prefer` only. Never fall back to
    # `locations_allow` here — that list historically contained "remote",
    # which would double-count against `remote_bonus` and read as a place.
    if prefer:
        pref_match = next((t for t in prefer if _hit(loc, t)), None)
        if pref_match:
            boost = _num(prefs, "location_prefer_boost", 1)
            s.score += boost
            s.reasons.append([f"pref:{pref_match}", boost])

    if remote and _num(prefs, "remote_bonus", 0):
        bonus = _num(prefs, "remote_bonus", 0)
        s.score += bonus
        s.reasons.append(["remote", bonus])

    return s


def reasons_text(sc: Score) -> str:
    """Human-readable 'why' line for digests, e.g. 'title:python +3 · loc:berlin +2'."""
    return " · ".join(f"{label} +{pts:g}" for label, pts in sc.reasons)
