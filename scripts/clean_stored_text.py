"""Strip markup from descriptions already stored, once.

The store now cleans text on the way in, but rows written before that change keep
whatever the board sent: Greenhouse's adverts were HTML, and the audit read them
as zero prose while the text was there all along.

Re-scanning would fix them, board by board and only for what the board still
lists. This fixes the rows that are already here, which is what the window reads.
Idempotent: it only touches rows whose description contains a tag.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobscout import db as dbm                     # noqa: E402
from jobscout.config import load_config            # noqa: E402
from jobscout.textutil import clean_html_lines     # noqa: E402


def main() -> int:
    cfg = load_config()
    conn = dbm.connect(cfg.root / "jobscout.db")
    rows = conn.execute(
        "SELECT id, description FROM postings WHERE description LIKE '%<%>%'"
    ).fetchall()
    changed = 0
    for row in rows:
        body = clean_html_lines(row["description"])
        if body and body != row["description"]:
            conn.execute("UPDATE postings SET description=? WHERE id=?",
                         (body, row["id"]))
            changed += 1
        if changed and changed % 500 == 0:
            conn.commit()
            print(changed, "cleaned", flush=True)
    conn.commit()
    print(f"{len(rows)} rows had markup, {changed} rewritten", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
