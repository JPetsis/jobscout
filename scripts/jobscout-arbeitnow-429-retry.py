"""Slow one-off retry of the arbeitnow URLs that came back HTTP 429.

Paces at 1.5 s per URL (the 0.6 s default burst is what re-triggered the
limit) and only re-checks rows whose stored state is 'unknown' with a
429 note, so nothing else in the database is touched. Standalone: run with
`uv run python /path/to/retry_arbeitnow_429.py`.
"""
import json
import pathlib
import sqlite3
import sys
import time

ROOT = pathlib.Path("/Users/ioannis.petsis/Projekte/Personal/jobscout")
sys.path.insert(0, str(ROOT))
from jobscout import liveness                                    # noqa: E402
from jobscout.config import home                                 # noqa: E402
from jobscout.db import connect, update_verify                    # noqa: E402
from jobscout.http import Fetcher                                # noqa: E402

DELAY = 1.5
conn = connect(home() / "jobscout.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT id, url, title FROM postings "
    "WHERE source='arbeitnow' AND verify_status='unknown' "
    "AND verify_note LIKE '%429%' ORDER BY id").fetchall()
print(f"retrying {len(rows)} arbeitnow 429 rows at {DELAY}s pacing", flush=True)

fetcher = Fetcher(host_delay=DELAY, retries=1)
counts = {"ok": 0, "dead": 0, "unknown": 0}
changed = []
for n, r in enumerate(rows, 1):
    state, note = liveness.check(fetcher, r["url"])
    update_verify(conn, r["id"], state, note)
    counts[state] += 1
    if state != "unknown":
        changed.append((r["id"], state, note, r["title"]))
    if n % 25 == 0:
        print(f"  {n}/{len(rows)}  {counts}", flush=True)
    if n < len(rows):
        time.sleep(DELAY)

summary = {"checked": len(rows), **counts, "changed": changed}
out = ROOT / "jobscout-arbeitnow-429-retry.json"
out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\nchecked {len(rows)} · {counts}  → {out}", flush=True)
