"""Platform API discovery for blocked job URLs.

Given a posting URL that a bot wall refuses, work out whether the platform
behind it publishes a machine interface we can verify against instead.

Two shapes are supported, in order of preference:

  per-item   a documented public endpoint for one posting
             e.g. api.lever.co/v0/postings/{co}/{uuid}
  membership the platform's own board feed, checked for the posting's id
             used where no per-item endpoint exists (larajobs, ashby)

The second shape is the general one. It is slower per row but it is what
turned larajobs from a permanent 401 into a verified row, and it applies to
any board whose HTML is walled but whose feed is open.

Read-only: probes public endpoints, writes nothing.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
TIMEOUT = 12.0


def _get(url: str, delay: float = 0.4) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:                                    # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


# --- platform recognisers -------------------------------------------------
# Each returns (platform, id, api_url) or None. Order matters: the more
# specific host checks must precede the generic subdomain ones.

def _greenhouse(m):
    slug, jid = m.group(1), m.group(2)
    return "greenhouse", jid, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}"


def _lever(m):
    co, pid = m.group(1), m.group(2)
    return "lever", pid, f"https://api.lever.co/v0/postings/{co}/{pid}"


def _ashby(m):
    co, jid = m.group(1), m.group(2)
    board = f"https://api.ashbyhq.com/posting-api/job-board/{co}"
    return "ashby", jid, board


def _smartrecruiters(m):
    co, jid = m.group(1), m.group(2)
    return "smartrecruiters", jid, f"https://api.smartrecruiters.com/v1/companies/{co}/postings/{jid}"


def _recruitee(m):
    co, slug = m.group(1), m.group(2)
    return "recruitee", slug, f"https://{co}.recruitee.com/api/offers/{slug}"


def _workable(m):
    # Two URL shapes exist and both are live:
    #   apply.workable.com/<account>/j/<CODE>   (canonical, account is the board)
    #   apply.workable.com/j/<CODE>            (short link, no account segment)
    # The short link carries no board name, so the board feed cannot be derived
    # from the URL alone; probe the canonical widget endpoint on the code itself.
    if m.group("account"):
        co, code = m.group("account"), m.group("code")
        feed = f"https://apply.workable.com/api/v2/accounts/{co}/jobs"
    else:
        co, code = "-", m.group("code")
        feed = f"https://apply.workable.com/api/v1/widget/accounts/{code}"
    return "workable", code, feed


def _breezy(m):
    co, jid = m.group(1), m.group(2)
    return "breezy", jid, f"https://{co}.breezy.hr/json"


def _larajobs(m):
    jid = m.group(1)
    return "larajobs", jid, "https://larajobs.com/api/jobs"


PLATFORMS = [
    (_greenhouse, re.compile(r"boards\.greenhouse\.io/([\w-]+)/jobs/(\d+)")),
    (_lever, re.compile(r"jobs\.lever\.co/([\w.-]+)/([\w-]+)")),
    (_ashby, re.compile(r"jobs\.ashbyhq\.com/([\w-]+)/([\w-]+)")),
    (_smartrecruiters, re.compile(r"jobs\.smartrecruiters\.com/([\w-]+)/(\d+)")),
    (_recruitee, re.compile(r"([\w-]+)\.recruitee\.com/o/([\w-]+)")),
    (_workable, re.compile(r"apply\.workable\.com/(?:(?P<account>[\w-]+)/)?j/(?P<code>[\wA-Z]+)")),
    (_breezy, re.compile(r"([\w-]+)\.breezy\.hr/(?:jobs/)?([\w-]+)")),
    (_larajobs, re.compile(r"larajobs\.com/job/(\d+)")),
]

# which platforms must be checked for id-membership rather than fetched direct
MEMBERSHIP = {"ashby", "workable", "larajobs", "breezy"}


def identify(url: str) -> tuple[str, str, str] | None:
    for fn, rx in PLATFORMS:
        m = rx.search(url)
        if m:
            return fn(m)
    return None


def _ids_in(payload: str) -> set[str]:
    """Pull every plausible id string out of a JSON feed."""
    out: set[str] = set()
    try:
        data = json.loads(payload)
    except Exception:                                          # noqa: BLE001
        return set()
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (str, int)) and k.lower() in {
                        "id", "uuid", "requisitionid", "jobid", "shortcode",
                        "reference", "code", "slug", "offerid", "externalid"}:
                    out.add(str(v))
                else:
                    stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return out


def probe(url: str, delay: float = 0.4) -> dict:
    """Return a verdict for one posting URL. Never raises."""
    hit = identify(url)
    if not hit:
        return {"url": url, "platform": None, "ok": False, "why": "no platform matched"}
    platform, ident, api = hit
    time.sleep(delay)

    status, body = _get(api, delay)
    rec = {"url": url, "platform": platform, "id": ident, "api": api,
           "http": status, "mode": "per-item"}

    if platform in MEMBERSHIP:
        rec["mode"] = "membership"
        if status != 200:
            rec["ok"] = False
            rec["why"] = f"feed HTTP {status}"
            return rec
        ids = _ids_in(body)
        rec["feed_size"] = len(ids)
        rec["ok"] = ident in ids
        rec["why"] = ("id present in feed" if rec["ok"]
                      else f"id absent from feed of {len(ids)}")
        return rec

    if status == 200:
        rec["ok"] = True
        rec["why"] = "per-item endpoint 200"
    elif status in (401, 403, 429):
        rec["ok"] = False
        rec["why"] = f"API also walled (HTTP {status})"
    elif status == 404:
        rec["ok"] = False
        rec["why"] = "API 404 -> posting genuinely gone"
    else:
        rec["ok"] = False
        rec["why"] = f"HTTP {status}"
    return rec


def main() -> int:
    urls = [ln.strip() for ln in sys.stdin if ln.strip() and not ln.startswith("#")]
    if not urls:
        print("feed URLs on stdin", file=sys.stderr)
        return 2
    results = [probe(u) for u in urls]
    ok = sum(1 for r in results if r["ok"])
    for r in results:
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"{mark} {r['platform'] or '-':<15} {r.get('mode', '-'):<10} "
              f"{r.get('why', '')}")
    print(f"\n{ok}/{len(results)} verifiable via a public API")
    by_plat: dict[str, list[int]] = {}
    for r in results:
        by_plat.setdefault(r["platform"] or "-", []).append(1 if r["ok"] else 0)
    for p, v in sorted(by_plat.items(), key=lambda kv: -sum(kv[1])):
        print(f"  {p:<16} {sum(v)}/{len(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
