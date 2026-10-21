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
    "instaffo", "ycombinator", "wellfound",
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
    "talent": "DACH", "instaffo": "DACH",
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
    "ycombinator": "Global and remote", "wellfound": "Global and remote",
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


# WHERE THE VACANCY COMES FROM: a channel where the employer or the institution
# publishes its OWN vacancy, rather than a board republishing somebody else's.
# The value is the reason, shown verbatim in the app, because the claim has to
# be the true one for that particular board:
#
#   "the employer's own board"           we poll the employer's hiring system
#   "... publishes its own vacancies"    the field's standard announcement
#                                        channel, where the ad is placed BY
#                                        the employer
#
# DELIBERATELY NARROW. xing, linkedin, stepstone, arbeitnow and the remote
# boards also carry ads the employer placed, and they also carry republished
# and agency copies of the same role. A label that is right for some of a
# source's rows and wrong for others is worse than no label, so those stay out.
# `test_every_source_is_classified` fails when a source is added without a
# decision, and unlisted names are simply not direct.
DIRECT_SOURCES = {
    # the employer's own applicant-tracking board: one employer each, and the
    # place the posting appears before it is syndicated anywhere
    "greenhouse": "the employer's own board",
    "ashby": "the employer's own board",
    "recruitee": "the employer's own board",
    "lever": "the employer's own board",
    "smartrecruiters": "the employer's own board",
    "workable": "the employer's own board",
    "teamtailor": "the employer's own board",
    "workday": "the employer's own board",
    "join": "the employer's own board",
    # research and higher education: the institution places the advert here
    "euraxess": "the EU research portal, where institutions publish their own vacancies",
    "academics": "the UK academic board, where institutions publish their own vacancies",
    "myscience": "the German research portal, where institutions publish their own vacancies",
    "wissenschaftskarriere": "the German academic board, where institutions publish their own vacancies",
    # public sector: the state's own hiring site for its own vacancies
    "usajobs": "the US government's own hiring site",
    "governmentjobs": "the public-sector hiring system employers publish into",
}


def is_direct(name: str) -> bool:
    """Is this a channel where the employer publishes the vacancy itself?"""
    return name in DIRECT_SOURCES


# The sources deliberately LEFT OUT, named rather than implied: a new board has
# to be classified on purpose, and `test_every_source_is_classified` fails until
# it is. Not being direct is the safe default (no badge, no claim), so this list
# exists to record the decision, not to change behaviour.
NOT_DIRECT = frozenset({
    # DACH boards and job centres: employers post here, and so do agencies and
    # republishers, so a per-row claim is impossible
    "stepstone", "xing", "stellenanzeigen", "karriere_at", "jobs_ch", "ams",
    "jobsat", "jobscout24", "jobware", "englishjobs", "talent",
    "berlinstartupjobs", "germantechjobs", "swissdevjobs", "jobup",
    "arbeitnow", "instaffo",
    # startup boards: the employer usually posts, but recruiters and agencies do
    # too, so a per-row "direct" claim would sometimes be wrong
    "ycombinator", "wellfound",
    # European and US boards that mix sources
    "eures", "themuse", "adzuna",
    # worldwide and remote boards, which mix employer ads with republished ones
    "linkedin", "hn", "remotive", "wwr", "remoteok", "larajobs", "workingnomads",
    "nodesk", "himalayas", "jobicy", "wordpressjobs", "pythonjobs", "fossjobs",
})


def direct_note(name: str) -> str:
    """Why this source counts as direct, in words the app can show."""
    return DIRECT_SOURCES.get(name, "")


def iter_sources(enabled):
    """Yield (name, module) for the enabled sources, in config order."""
    for name in enabled:
        mod = REGISTRY.get(name)
        if mod is None:
            print(f"warn: unknown source '{name}' in [sources] enabled — skipped")
            continue
        yield name, mod
