"""Data model for a single job posting."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Posting:
    source: str
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    external_id: str = ""
    posted_at: str = ""      # ISO date or timestamp fragment; "" when unknown
    expires_at: str = ""     # publisher-declared expiry (ISO date); "" when unknown
    description: str = ""    # plain text, possibly truncated
    compensation: str = ""   # raw compensation text from the source, if any
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    salary_period: str = ""            # hour | day | month | year
    salary_annual_max: float | None = None
    salary_raw: str = ""               # the text the parse came from
    extra: dict = field(default_factory=dict)

    def is_valid(self) -> bool:
        return bool((self.title or "").strip())
