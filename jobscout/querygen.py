"""Build source queries at runtime from the profile, instead of hardcoding them.

WHY THIS EXISTS
---------------
The fetch funnel used to be 16 hand-written software queries baked into the
config template. Two consequences, both silent:

* A user in any other field (archaeology, nursing, finance) inherited software
  keywords and fetched ZERO postings, with no error to explain it.
* Even a software user silently missed roles: `keywords: softwareentwickler`
  on the five German boards stored 302 rows and not one PHP title, because
  generic German title vocabulary returns Java/.NET/ERP/SAP. Adding
  `keywords: php` on the same day returned 98 PHP postings.

The fix is to stop treating "which keywords" as configuration and derive it
from the profile the user already wrote. `preferences` already knows the role
vocabulary (`title_terms`) and the technologies (`stack_preferred` /
`stack_terms`); those ARE the queries.

DESIGN
------
Three tiers, cheapest and most general first, so a narrow profile still gets
breadth and a broad profile does not drown in requests:

1. `broad`  - one query with an EMPTY keyword per capable source. Verified live:
   stepstone/xing/stellenanzeigen/karriere_at/jobs_ch all return 15-20 recent
   postings for `keywords: ""`, i.e. a chronological sweep. This is what makes
   the tool usable for any field: the profile decides the RANKING, and this
   decides the RECALL.
2. `role`   - the profile's `title_terms`, which are role words by construction
   ("engineer", "entwickler"). These catch the generic-title case.
3. `stack`  - the profile's technologies at their highest weights. This is the
   tier that found the 98 PHP rows. Only terms weighing >= `stack_query_min`
   (default 2) become queries, so table stakes do not each cost a request.

Everything is capped by `query_budget` per source so a large profile cannot
explode a scan, and de-duplicated because role and stack tiers overlap
("engineer" appears in both).

`enabled: false` on a source opts it out of generated queries entirely.
`queries:` already present in the config is respected: generated queries are
ADDED to it, never replace it, so hand-tuned queries keep working.
"""
from __future__ import annotations

# Sources verified to accept an empty keyword and return a recent-postings
# sweep (measured 2026-10-03; extended 2026-10-15: jobup, jobscout24,
# jobware, talent and governmentjobs all return a sweep for an empty
# keyword). A source not listed here simply gets no `broad` query, which is
# safe: it keeps role/stack queries.
BROAD_CAPABLE = {
    "stepstone", "xing", "stellenanzeigen", "karriere_at",
    "jobs_ch", "linkedin", "ams", "eures", "jobsat",
    "jobup", "jobscout24", "jobware", "talent", "governmentjobs",
    "myscience",
}

# Adapters that take `{"keywords", "location", "pages"}` verbatim. Anything
# else (RSS/board sources like hn, larajobs, himalayas) is not query-driven
# and must not be given generated queries. Each of the 2026-10-15 additions
# maps `keywords` to its own parameter (query/SearchWhat/q/k/keyword);
# jobware, governmentjobs and myscience do NOT honour `location` and say so
# loudly in fetcher.errors instead of dropping it silently.
QUERY_DRIVEN = {
    "stepstone", "xing", "stellenanzeigen", "karriere_at",
    "jobs_ch", "linkedin", "ams", "eures", "jobsat",
    "jobup", "jobscout24", "jobware", "talent", "governmentjobs",
    "myscience",
}

# `location` semantics differ per board, so one profile value cannot be shared.
# These are the neutral defaults; a source with `location_mode: query` in the
# config takes the location from the query dict instead.
_LOCATION_DEFAULTS = {
    "stepstone": "",            # ignores the filter anyway (verified: Berlin -> Frankfurt)
    "xing": "deutschland",
    "stellenanzeigen": "",
    "karriere_at": "",
    "jobs_ch": "",
    "linkedin": "Deutschland",
}


def _norm_list(values, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        if not isinstance(v, (str, int, float)):
            continue
        s = str(v).strip()
        # single-token role words only: multi-word phrases make poor search
        # keywords on most boards and blow the budget without paying off
        if not s or len(s) > 32 or " " in s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _stack_query_terms(prefs: dict, min_weight: float, limit: int) -> list[str]:
    """Technologies worth querying: highest weights first, ties alphabetical.

    `stack_preferred` is the user's stated interest, so it comes first; a term
    that is only on the flat `stack_terms` list is weaker evidence of intent and
    is used only if there is budget left.
    """
    scored: list[tuple[float, str]] = []
    weighted = prefs.get("stack_preferred") or {}
    if isinstance(weighted, dict):
        for term, weight in weighted.items():
            try:
                w = float(weight)
            except (TypeError, ValueError):
                continue
            if w >= min_weight and isinstance(term, str):
                scored.append((w, term))
    for term in prefs.get("stack_terms") or []:
        if isinstance(term, str):
            scored.append((1.0, term))
    scored.sort(key=lambda kv: (-kv[0], kv[1].lower()))
    return _norm_list([t for _, t in scored], limit)


def generate_queries(cfg, name: str, prefs: dict | None = None) -> list[dict]:
    """Generated queries for one source, in cost order. Pure; no network.

    Returns [] when the source is not query-driven, is disabled by config, or
    the profile has nothing to say (`profiles.generate_queries: false`).
    """
    if name not in QUERY_DRIVEN:
        return []
    settings = (cfg.sources_cfg or {}).get(name) or {}
    if settings.get("generate_queries") is False:
        return []
    prefs = prefs if prefs is not None else (cfg.prefs or {})

    budget = settings.get("query_budget")
    if budget is None:
        # `prefs` may be passed explicitly (profiles, tests, goals), so it is
        # the single source of truth here -- never fall back to cfg.prefs.
        budget = prefs.get("query_budget", 6)
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        budget = 6
    if budget <= 0:
        return []

    pages = int(settings.get("query_pages", 1) or 1)
    location = _LOCATION_DEFAULTS.get(name, "")
    queries: list[dict] = []

    def add(keywords: str) -> bool:
        if len(queries) >= budget:
            return False
        for q in queries:                       # de-dup case-insensitively
            if str(q.get("keywords", "")).lower() == keywords.lower():
                return False
        q = {"keywords": keywords, "pages": pages}
        if location:
            q["location"] = location
        queries.append(q)
        return True

    # 1. broad sweep: the field-agnostic safety net
    if name in BROAD_CAPABLE and settings.get("broad_query", True):
        add("")

    # 2. role vocabulary from the profile's own title_terms
    role_limit = int(settings.get("query_roles", 3) or 3)
    for term in _norm_list(prefs.get("title_terms"), role_limit):
        add(term)

    # 3. technologies, weighted by stated interest
    min_w = float(prefs.get("stack_query_min", 2) or 2)
    for term in _stack_query_terms(prefs, min_w, budget):
        add(term)

    return queries[:budget]


def merge_queries(cfg, name: str, prefs: dict | None = None) -> list[dict]:
    """Hand-written queries for a source, with generated ones appended.

    Hand-written queries keep priority and are never dropped: a user who tuned
    them explicitly should not have that work overwritten by a heuristic.
    """
    settings = (cfg.sources_cfg or {}).get(name) or {}
    hand = [q for q in (settings.get("queries") or []) if isinstance(q, dict)]
    if settings.get("generate_queries") is False:
        return hand
    generated = generate_queries(cfg, name, prefs)
    if not generated:
        return hand
    budget = settings.get("query_budget")
    if budget is None:
        budget = (prefs or cfg.prefs or {}).get("query_budget", 6)
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        budget = 6
    seen = {str(q.get("keywords", "")).strip().lower() for q in hand}
    out = list(hand)
    for q in generated:
        key = str(q.get("keywords", "")).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= budget:
            break
    return out