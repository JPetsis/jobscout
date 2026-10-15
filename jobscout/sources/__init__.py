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
]

REGISTRY = {name: import_module(f".{name}", __package__) for name in SOURCE_NAMES}


def iter_sources(enabled):
    """Yield (name, module) for the enabled sources, in config order."""
    for name in enabled:
        mod = REGISTRY.get(name)
        if mod is None:
            print(f"warn: unknown source '{name}' in [sources] enabled — skipped")
            continue
        yield name, mod
