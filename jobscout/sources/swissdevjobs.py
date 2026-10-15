"""SwissDevJobs — the Swiss sibling of GermanTechJobs (same RSS platform).

One implementation: germantechjobs.parse_items() and germantechjobs.fetch_feed()
do the work here too; only the feed URL and the source name differ.
"""
from __future__ import annotations

from ..http import Fetcher
from ..models import Posting
from .germantechjobs import fetch_feed, parse_items

NAME = "swissdevjobs"
LABEL = "SwissDevJobs"
FEED = "https://swissdevjobs.ch/rss"


def parse(text: str) -> list[Posting]:
    return parse_items(text, NAME)


def fetch(settings: dict, fetcher: Fetcher) -> list[Posting]:
    return fetch_feed(fetcher, FEED, NAME)
