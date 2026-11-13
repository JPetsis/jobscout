"""Liveness checks: is a posting still open?

Board pages for Greenhouse/Ashby/Lever are JS shells that answer 200 for any
URL, so known ATS job URLs get a targeted API probe instead. Everything else
falls back to a generic HTTP probe plus a small set of "this posting is gone"
phrases. 404/410 count as dead; 401/403/429 and network errors count as
unknown (a blocked request is not evidence the posting is gone).
"""
from __future__ import annotations

import datetime
import json
import re
import urllib.parse

from .http import FetchError, Fetcher

DEAD_MARKERS = re.compile(
    r"(no longer accepting applications|no longer available|position has been filled|"
    r"this (?:job|position|posting) (?:is|has been) (?:no longer|filled|closed|removed)|"
    r"job (?:posting )?(?:not found|is closed|has expired)|offer (?:has )?expired|"
    r"diese stelle (?:ist|wurde) (?:nicht mehr|bereits besetzt|besetzt)|"
    r"nicht mehr verfügbar|bereits besetzt|no such item)",
    re.I,
)

DEAD_STATUS = {404, 410}
SOFT_STATUS = {401, 403, 429}

# Hosts that serve public JSON APIs fine but put every HTML page behind an
# interactive anti-bot challenge, so no posting URL on them can ever be probed.
# Verified by hand: himalayas.app/ and /jobs/api return 200, while every
# /companies/... posting page returns 403 to plain HTTP AND to a real browser
# (Cloudflare Turnstile "Verify you are human"). Re-probing is pointless, so
# these get a permanent note instead of re-queuing on every pass.
_CHALLENGE_HOSTS = {
    "himalayas.app": "Cloudflare Turnstile",
}


def _is_challenge_host(url: str) -> bool:
    host = url.split("/")[2] if "//" in url else ""
    return host in _CHALLENGE_HOSTS


def _expiry_verdict(expires_at: str) -> tuple[str, str] | None:
    """Judge liveness from a publisher-declared expiry, or None to fall through.

    A date already in the past is a verdict: the posting is dead, full stop.

    A FUTURE date is NOT a verdict about today, and must never report "ok".
    Measured on real data: himalayas stamps a flat 60-day expiry, so all 404
    tracked rows sat 24-60 days out — a "trust the near future" rule would have
    accepted exactly none of them while looking clever. A date that far out
    says the posting existed, not that it exists now.

    What it DOES buy is the only thing that matters on a walled source: the
    moment the date passes, the row is marked dead with no network call and no
    challenge. Liveness is decided by decay rather than by probe.

    A missing or unparseable value must NEVER expire anything: "" sorts before
    every real date, so a naive string comparison would kill every row.
    """
    raw = (expires_at or "").strip()[:10]
    try:
        when = datetime.date.fromisoformat(raw)
    except ValueError:
        return None
    if when < datetime.date.today():
        return "dead", f"publisher expiry passed ({raw})"
    return None

_UUID = r"[0-9a-fA-F-]{36}"
_GH_JOB_RE = re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([^/?#]+)/jobs/(\d+)")
_LEVER_RE = re.compile(rf"jobs\.lever\.co/([^/?#]+)/({_UUID})")
_ASHBY_RE = re.compile(rf"jobs\.ashbyhq\.com/([^/?#]+)/({_UUID})")
_SR_RE = re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)/(\d+)")
_AMS_RE = re.compile(rf"jobs\.ams\.at/public/emps/jobs/({_UUID})")
# larajobs serves its own /job/<id> page behind a Cloudflare 401, but the
# public /api/jobs feed is open and lists every live posting with its id, so
# membership in that feed is a real verdict. No single-job endpoint exists.
_EURES_RE = re.compile(r"europa\.eu/eures/portal/jv-se/jv-details/([^/?#]+)")
_LARAJOBS_RE = re.compile(r"larajobs\.com/job/(\d+)")
_LARAJOBS_API = "https://larajobs.com/api/jobs"


#: An identifier a board puts in the advert's path ("/jobs/10032227").
_ID_IN_PATH = re.compile(r"(?:^|[/-])(\d{4,})(?:[/?#-]|$)")


def _redirected_away(url: str, final: str) -> str:
    """The page a board served INSTEAD of the advert, or "" when it is the one.

    A board that answers 200 and sends an expired advert to a place page is a
    soft 404: the status is fine, the body has none of the usual phrases, and
    the advert is gone. The evidence is the identifier -- the requested path had
    one and the page we landed on does not.
    """
    if not final or final == url:
        return ""
    asked = urllib.parse.urlsplit(url)
    got = urllib.parse.urlsplit(final)
    ids = set(_ID_IN_PATH.findall(asked.path))
    if not ids:
        return ""
    hay = got.path + ("?" + got.query if got.query else "")
    if any(pid in hay for pid in ids):
        return ""
    return got.path or got.netloc


def _probe(fetcher: Fetcher, url: str, headers: dict | None = None) -> tuple[int, str] | tuple[None, str]:
    try:
        return fetcher.get(url, headers=headers)
    except FetchError as e:
        return None, e.message[:50]


def _ats_check(fetcher: Fetcher, url: str) -> tuple[str, str] | None:
    """Targeted per-job probes for known ATS boards; None when not applicable."""
    m = _GH_JOB_RE.search(url)
    if m:
        board, job_id = m.group(1), m.group(2)
        status, _ = _probe(fetcher, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}")
        if status is None:
            return "unknown", "greenhouse api unreachable"
        if status == 404:
            return "dead", "greenhouse api says the job is gone"
        return ("ok", "") if status == 200 else ("unknown", f"greenhouse api HTTP {status}")

    m = _LEVER_RE.search(url)
    if m:
        board, job_id = m.group(1), m.group(2)
        status, _ = _probe(fetcher, f"https://api.lever.co/v0/postings/{board}/{job_id}")
        if status is None:
            return "unknown", "lever api unreachable"
        if status == 404:
            return "dead", "lever api says the posting is gone"
        return ("ok", "") if status == 200 else ("unknown", f"lever api HTTP {status}")

    m = _ASHBY_RE.search(url)
    if m:
        board, job_id = m.group(1), m.group(2)
        status, text = _probe(fetcher, f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        if status is None:
            return "unknown", "ashby api unreachable"
        if status != 200:
            return "unknown", f"ashby board HTTP {status}"
        try:
            jobs = json.loads(text).get("jobs") or []
        except (ValueError, AttributeError):
            return "unknown", "ashby board: unexpected answer"
        if not jobs:
            return "unknown", "ashby board lists no jobs at all"
        needle = job_id.lower()
        if needle in {str(j.get("id", "")).lower() for j in jobs}:
            return "ok", ""
        if needle in " ".join(str(j.get("jobUrl", "")) for j in jobs).lower():
            return "ok", ""
        return "dead", "not on the ashby board anymore"

    m = _LARAJOBS_RE.search(url)
    if m:
        job_id = m.group(1)
        status, text = _probe(fetcher, _LARAJOBS_API)
        if status is None:
            return "unknown", "larajobs api unreachable"
        if status != 200:
            return "unknown", f"larajobs api HTTP {status}"
        try:
            jobs = json.loads(text)
        except ValueError:
            return "unknown", "larajobs api: unexpected answer"
        if not isinstance(jobs, list) or not jobs:
            return "unknown", "larajobs api lists no jobs at all"
        if job_id in {str(j.get("id", "")) for j in jobs if isinstance(j, dict)}:
            return "ok", ""
        return "dead", "not in the larajobs feed anymore"

    m = _AMS_RE.search(url)
    if m:
        # Every jobs.ams.at path returns HTTP 200 with the same 19,858-byte SPA
        # shell — a real job, a fabricated uuid and a nonexistent page are
        # byte-identical — so the page proves nothing. The detail API
        # /joboffer/uuid/<uuid> is authoritative: 200 + record when the offer is
        # still up, 404 once it is withdrawn. It needs the same three signed
        # headers as the search endpoint (see sources/ams.py).
        from .sources import ams as _ams
        offer_uuid = m.group(1)
        path = f"/public/emps/api/joboffer/uuid/{offer_uuid}"
        status, _text = _probe(fetcher, f"https://jobs.ams.at{path}",
                               headers=_ams._token(path, {}))
        if status is None:
            return "unknown", "ams api unreachable"
        if status in (404, 410):
            return "dead", "ams api says the offer is gone"
        if status != 200:
            return "unknown", f"ams api HTTP {status}"
        return "ok", ""

    m = _EURES_RE.search(url)
    if m:
        # The portal page is client-rendered and answers 200 for a fabricated
        # id; the detail API is authoritative and returns the vacancy payload.
        import urllib.parse
        jv_id = urllib.parse.unquote(m.group(1))
        status, text = _probe(
            fetcher,
            f"https://europa.eu/eures/api/jv-searchengine/public/jv/id/{jv_id}?requestLang=en")
        if status is None:
            return "unknown", "eures api unreachable"
        if status in (404, 410):
            return "dead", "eures api says the vacancy is gone"
        if status != 200:
            return "unknown", f"eures api HTTP {status}"
        try:
            data = json.loads(text)
        except ValueError:
            return "unknown", "eures api: unexpected answer"
        if not data.get("id") and not data.get("jvProfiles"):
            return "dead", "eures api returned an empty vacancy"
        return "ok", ""

    m = _SR_RE.search(url)
    if m:
        board, job_id = m.group(1), m.group(2)
        status, _ = _probe(fetcher, f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{job_id}")
        if status is None:
            return "unknown", "smartrecruiters api unreachable"
        if status == 404:
            return "dead", "smartrecruiters api says the posting is gone"
        return ("ok", "") if status == 200 else ("unknown", f"smartrecruiters api HTTP {status}")

    return None


def check(fetcher: Fetcher, url: str, expires_at: str = "") -> tuple[str, str]:
    """(state, note), state in {'ok', 'dead', 'unknown'}.

    `expires_at` is a publisher-declared expiry date, when the source gives
    one. It is consulted BEFORE any network call, so a source whose pages are
    behind a bot challenge can still be judged: a date the publisher itself
    published needs no probe, and asking for one is not the same as defeating
    the challenge.
    """
    if expires_at:
        verdict = _expiry_verdict(expires_at)
        if verdict:
            return verdict
    if not url:
        return "unknown", "no url"
    targeted = _ats_check(fetcher, url)
    if targeted is not None:
        return targeted
    try:
        # A fetcher that cannot say where it landed (a test double, or a caller
        # that only implements the two-value get) is treated as never redirected.
        getter = getattr(fetcher, "get_final", None)
        if getter is not None:
            status, text, final = getter(url)
        else:
            status, text = fetcher.get(url)
            final = url
    except FetchError as e:
        return "unknown", f"fetch error ({e.message[:60]})"
    if status in DEAD_STATUS:
        return "dead", f"HTTP {status}"
    if status in SOFT_STATUS:
        # A 403 is ambiguous: it is both "your request was blocked" and the
        # status every soft-404 page returns. Only treat it as structural when
        # the source is KNOWN to sit behind an interactive bot challenge,
        # because then re-probing can never succeed and the row would re-queue
        # on every pass forever.
        if status == 403 and _is_challenge_host(url):
            return "unknown", f"HTTP 403 ({_CHALLENGE_HOSTS[url.split('/')[2]]} challenge, unverifiable)"
        return "unknown", f"HTTP {status} (blocked?)"
    if status != 200:
        return "unknown", f"HTTP {status}"
    m = DEAD_MARKERS.search(text or "")
    if m:
        return "dead", f"page says: {m.group(0)[:48]}"
    moved = _redirected_away(url, final)
    if moved:
        # 200 + none of the dead markers, but the advert is not at that URL any
        # more: karriere.at answers an expired ad with its city page.
        return "dead", f"redirected to {moved}"
    return "ok", ""

