"""Back up the workspace to a single portable archive.

WHY THIS EXISTS, and why the destination is OUTSIDE the workspace:
a deep-uninstaller deleted `~/.jobscout/` because the directory is named after
the app (see docs/INSTALL.md). Anything a user keeps to survive that cannot
live inside `~/.jobscout/`, or the same tool removes the backup too. So the
default destination is `~/jobscout-backups/`, deliberately a sibling of the
workspace rather than a child of it.

WHY sqlite's backup API instead of copying the file:
`jobscout.db` is a live database and the desktop app can be running while this
runs. A byte copy taken mid-write can capture a torn page, and the failure is
silent: the archive looks fine until the day it is restored. `Connection.backup`
takes a consistent snapshot of a database that is open elsewhere, which is
exactly the situation this has to survive. The snapshot is integrity-checked
before it is archived, because a backup that cannot be restored is worse than
no backup: it is a false assurance.
"""
from __future__ import annotations

import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

DB_NAME = "jobscout.db"
CONFIG_NAME = "config.yaml"
DEFAULT_DIR = "jobscout-backups"
MANIFEST = "MANIFEST.txt"


def default_destination() -> Path:
    """A sibling of the workspace, never a child of it."""
    return Path.home() / DEFAULT_DIR


def _snapshot_db(src: Path, dst: Path) -> None:
    """Consistent copy of a possibly-open database, verified before returning."""
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)          # safe while another process holds it
        finally:
            target.close()
    finally:
        source.close()

    check = sqlite3.connect(str(dst))
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        raise RuntimeError(f"the database snapshot failed its integrity check: {result}")


def _counts(db: Path) -> dict:
    """A few numbers to put in the manifest, so a user can confirm the backup
    is really theirs and not an empty shell."""
    out: dict = {}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        out["postings"] = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
        out["matches"] = conn.execute(
            "SELECT COUNT(*) FROM postings WHERE filtered=0 AND dup_of IS NULL "
            "AND score >= (SELECT COALESCE(MIN(score),0) FROM postings)"
        ).fetchone()[0]
        out["marked"] = conn.execute(
            "SELECT COUNT(*) FROM postings WHERE status != 'new'").fetchone()[0]
        row = conn.execute("SELECT MAX(id) FROM runs").fetchone()
        out["last_run"] = row[0] if row and row[0] is not None else 0
    finally:
        conn.close()
    return out


def backup(root: Path, dest_dir: Path, *, include_reports: bool = True,
           now: datetime | None = None) -> dict:
    """Write one archive. Returns a summary dict; raises on failure.

    Raises rather than returning a soft error: a backup that quietly produced
    nothing is the one failure mode a user cannot detect until they need it.
    """
    db = root / DB_NAME
    if not db.is_file():
        raise FileNotFoundError(f"no database at {db}")

    dest_dir = Path(dest_dir).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H%M")
    archive = dest_dir / f"jobscout-{stamp}.tar.gz"
    # Never overwrite an existing backup. Two runs inside the same minute would
    # otherwise collide and the first would be silently replaced, which loses a
    # restore point in the one command whose whole job is not losing things.
    if archive.exists():
        for n in range(2, 1000):
            candidate = dest_dir / f"jobscout-{stamp}-{n}.tar.gz"
            if not candidate.exists():
                archive = candidate
                break
        else:
            raise RuntimeError(f"too many backups for {stamp} in {dest_dir}")

    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / DB_NAME
        _snapshot_db(db, snap)
        counts = _counts(snap)

        cfg = root / CONFIG_NAME
        if not cfg.is_file():
            raise FileNotFoundError(f"no config at {cfg}")

        manifest = [
            "jobscout backup",
            f"created   {datetime.now().isoformat(timespec='seconds')}",
            f"source    {root}",
            f"postings  {counts['postings']}",
            f"marked    {counts['marked']} (saved/applied/dismissed)",
            f"last run  #{counts['last_run']}",
            "",
            "Restore: stop jobscout, unpack over ~/.jobscout/, restart.",
            "  tar xzf this-file.tar.gz -C ~/.jobscout",
        ]

        # deterministic-ish: only these three entries, so the archive contains
        # a config, a database and the reports, and nothing incidental
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(snap, arcname=DB_NAME)
            tar.add(cfg, arcname=CONFIG_NAME)
            reports = root / "reports"
            if include_reports and reports.is_dir():
                for f in sorted(reports.glob("*.md")):
                    tar.add(f, arcname=f"reports/{f.name}")
            mf = Path(tmp) / MANIFEST
            mf.write_text("\n".join(manifest) + "\n", encoding="utf-8")
            tar.add(mf, arcname=MANIFEST)

    # prove the archive is readable before telling anyone it worked
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    for required in (DB_NAME, CONFIG_NAME):
        if required not in names:
            raise RuntimeError(f"the archive is missing {required}: {archive}")

    return {
        "archive": archive,
        "bytes": archive.stat().st_size,
        "entries": names,
        "counts": counts,
    }


def list_backups(dest_dir: Path) -> list[dict]:
    dest_dir = Path(dest_dir).expanduser()
    if not dest_dir.is_dir():
        return []
    out = []
    for p in sorted(dest_dir.glob("jobscout-*.tar.gz"), reverse=True):
        st = p.stat()
        out.append({
            "path": p,
            "bytes": st.st_size,
            "created": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes"),
        })
    return out
