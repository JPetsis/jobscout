"""Resolve a DACH employer seed list to ATS board tokens, then verify each board.

Two stages, both polite and bounded:

  1. For each seed company, try its likely careers URL through
     ``companies.detect`` + ``companies.validate``. Detection is pattern-based
     over the URL and the page body; a token is only accepted when the board's
     public jobs API demonstrably returns roles.
  2. Write every verified (source, token, job-count) into a JSON report, so the
     results can be reviewed before anything is added to the config.

Nothing here mutates the real config. Run:

    uv run python scripts/dach-ats-sweep.py                  # seeds from below
    uv run python scripts/dach-ats-sweep.py seeds.json       # own seed list

Seeds are plain company domains; the careers paths are tried in order.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobscout.companies import detect, validate  # noqa: E402
from jobscout.http import Fetcher  # noqa: E402

# DACH tech employers, deliberately weighted towards Mittelstand and regional
# companies rather than household names. Domains only; careers paths are tried.
SEED_DOMAINS = [
    # TYPO3 / PHP / Laravel ecosystem
    "typo3.org", "b13.de", "m12services.net", "stark-online.de", "pixelink.de",
    "mediafactory.de", "form4you.de", "dachser.com",
    # German Mittelstand software / IT services
    "adesso.de", "valtech.com", "reply.com", "exensio.de", "cognite.de",
    "quantica.com", "arvato.com", "decathlon.com", "softgarden.de",
    "computacenter.com", "bechtle.com", "cancom.com", "datagroup.com",
    # Scale-ups and mid-market software houses
    "staffbase.com", "senacor.de", "openforlife.de", "lionsandwaves.com",
    "allthings.com", "getflow.de", "finleapconnect.com", "jellyduck.com",
    "viagogo.com", "aboutyou.com", "holidu.com", "avatrader.com",
    # Regional / local employers that actually hire developers
    "eurorisk.de", "openadx.com", "dematic.com", "ctrlp.com",
]

CAREERS_SUFFIXES = [
    "/careers", "/jobs", "/job", "/karriere", "/career", "/stellenangebote",
    "/stellenanzeigen", "/arbeitspl%C3%A4tze", "/joinus", "/join-us",
    "/work-with-us", "/about/careers", "/company/careers", "/en/careers",
]

OUT = Path(__file__).resolve().parents[1] / "dach-ats-sweep.json"
DELAY = 1.0          # polite spacing between company pages
ATS_DELAY = 1.2      # polite spacing between board API probes


def careers_urls(domain: str) -> list[str]:
    base = f"https://www.{domain}"
    return [base + s for s in CAREERS_SUFFIXES]


def main() -> int:
    seeds = json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else SEED_DOMAINS
    # a discovery pass must give up fast: 8s timeout, no retries, because a
    # dead careers host otherwise costs 8s x every retry before moving on
    fetcher = Fetcher(retries=0, timeout=8.0, host_delay=DELAY)
    rows: list[dict] = []
    seen_tokens: set[tuple[str, str]] = set()

    for domain in seeds:
        print(f".. {domain}", flush=True)
        found: list[tuple[str, str]] = []
        # URL-only pass first: no network cost, catches subdomains directly
        for url in careers_urls(domain):
            for src, tok in detect("", url):
                if (src, tok) not in seen_tokens:
                    found.append((src, tok))
        # then fetch a couple of likely pages and scan the body
        for url in careers_urls(domain)[:3]:
            if found:
                break
            try:
                status, body = fetcher.get(url)
            except Exception:
                continue
            if status != 200:
                continue
            time.sleep(DELAY)
            for src, tok in detect(body, url):
                if (src, tok) not in seen_tokens:
                    found.append((src, tok))
        for src, tok in found:
            seen_tokens.add((src, tok))
            ok, detail = validate(fetcher, src, tok)
            time.sleep(ATS_DELAY)
            print(f"{'OK ' if ok else 'no '} {src:<15} {tok:<28} {detail}", flush=True)
            rows.append({"domain": domain, "source": src, "token": tok,
                         "verified": ok, "detail": detail})
    OUT.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    good = [r for r in rows if r["verified"]]
    print(f"\n{len(good)} verified board(s) of {len(rows)} candidate(s) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
