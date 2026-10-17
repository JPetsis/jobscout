"""Source registry.

Every source module exposes:
  NAME      short id (matches config keys)
  LABEL     human-readable name
  fetch(settings, fetcher) -> list[Posting]      (network)
and optionally:
  parse(text_or_obj) -> list[Posting]            (pure, used by tests)
  fetch_detail(posting, fetcher) -> str | None   (full description on demand)
"""
from __future__ import annotations

from importlib import import_module

SOURCE_NAMES = [
    "linkedin", "stepstone", "xing", "stellenanzeigen", "karriere_at", "jobs_ch",
    "arbeitnow", "berlinstartupjobs", "ams", "eures", "jobsat", "hn", "remotive", "wwr",
    "remoteok", "larajobs", "workingnomads", "nodesk", "himalayas",
    "greenhouse", "ashby", "recruitee", "lever", "smartrecruiters", "workable",
    "themuse", "workday",
    "adzuna", "usajobs",
    "germantechjobs", "swissdevjobs", "jobup", "jobscout24", "jobware", "talent",
    "englishjobs", "governmentjobs", "jobicy", "teamtailor", "join",
    "wordpressjobs", "pythonjobs", "fossjobs",
    "academics", "wissenschaftskarriere", "euraxess", "myscience",
]

REGISTRY = {name: import_module(f".{name}", __package__) for name in SOURCE_NAMES}


# Which market each board serves, so the window can group them. This lives here
# rather than as a GROUP constant in 47 modules because a grouping is a
# RELATIONSHIP between boards: one table is reviewable at a glance, and
# re-grouping is one edit instead of 47. `test_sources_grouped` fails if a
# source is added without a home, so the decision cannot be forgotten.
GROUP_OTHER = "Other"

GROUP_ORDER = [
    "DACH",
    "US",
    "Europe (EU)",
    "Global and remote",
    "Academic and research",
    "Company boards",
    GROUP_OTHER,
]

GROUPS = {
    # Germany, Austria, Switzerland
    "stepstone": "DACH", "xing": "DACH", "stellenanzeigen": "DACH",
    "karriere_at": "DACH", "jobs_ch": "DACH", "arbeitnow": "DACH",
    "berlinstartupjobs": "DACH", "ams": "DACH", "jobsat": "DACH",
    "germantechjobs": "DACH", "swissdevjobs": "DACH", "jobup": "DACH",
    "jobscout24": "DACH", "jobware": "DACH", "englishjobs": "DACH",
    "talent": "DACH",
    # United States
    "themuse": "US", "usajobs": "US", "governmentjobs": "US",
    # European Union
    "eures": "Europe (EU)", "euraxess": "Europe (EU)",
    # worldwide, and remote-first boards
    "linkedin": "Global and remote", "hn": "Global and remote",
    "remotive": "Global and remote", "wwr": "Global and remote",
    "remoteok": "Global and remote", "larajobs": "Global and remote",
    "workingnomads": "Global and remote", "nodesk": "Global and remote",
    "himalayas": "Global and remote", "jobicy": "Global and remote",
    "adzuna": "Global and remote", "wordpressjobs": "Global and remote",
    "pythonjobs": "Global and remote", "fossjobs": "Global and remote",
    # research and higher education
    "academics": "Academic and research",
    "wissenschaftskarriere": "Academic and research",
    "myscience": "Academic and research",
    # per-company boards: you choose the employers, and they can be anywhere
    "greenhouse": "Company boards", "ashby": "Company boards",
    "recruitee": "Company boards", "lever": "Company boards",
    "smartrecruiters": "Company boards", "workable": "Company boards",
    "workday": "Company boards", "teamtailor": "Company boards",
    "join": "Company boards",
}


def group_of(name: str) -> str:
    """The market group for a source.

    An unlisted name returns GROUP_OTHER rather than being filed somewhere
    plausible: a new board silently appearing under the wrong market is worse
    than it appearing under "Other". `test_every_source_has_a_group` fails
    first, so this is a belt to that brace.
    """
    return GROUPS.get(name, GROUP_OTHER)


def iter_sources(enabled):
    """Yield (name, module) for the enabled sources, in config order."""
    for name in enabled:
        mod = REGISTRY.get(name)
        if mod is None:
            print(f"warn: unknown source '{name}' in [sources] enabled — skipped")
            continue
        yield name, mod
