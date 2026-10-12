"""Tests for `jobscout backup`.

The property that matters: a backup must be a COMPLETE and RESTORABLE copy,
including while the database is open by the desktop app. A backup that cannot
be restored is worse than none, because it is a false assurance.
"""
import sqlite3
import tarfile
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from jobscout import backup as bk

CONFIG = "# a documented config\n# another comment\npreferences:\n  min_score: 6\n"


def make_workspace(root: Path, *, postings=5, reports=2) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(CONFIG, encoding="utf-8")
    (root / "reports").mkdir(exist_ok=True)
    for i in range(reports):
        (root / "reports" / f"2026-01-0{i + 1}-matches.md").write_text(f"report {i}\n")

    conn = sqlite3.connect(str(root / "jobscout.db"))
    conn.execute("CREATE TABLE postings (id INTEGER PRIMARY KEY, title TEXT, "
                 "score REAL, filtered INTEGER DEFAULT 0, dup_of INTEGER, status TEXT)")
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT)")
    conn.execute("INSERT INTO runs (started_at) VALUES ('2026-01-01T00:00:00')")
    for i in range(postings):
        conn.execute(
            "INSERT INTO postings (title, score, status) VALUES (?,?,?)",
            (f"job {i}", 10.0 - i, "dismissed" if i == 0 else "new"))
    conn.commit()
    conn.close()
    return root


# --- the happy path --------------------------------------------------------

def test_backup_creates_a_verified_archive(tmp_path):
    root = make_workspace(tmp_path / "ws")
    res = bk.backup(root, tmp_path / "out", now=datetime(2026, 1, 2, 3, 4))

    assert res["archive"].is_file()
    assert res["archive"].name == "jobscout-2026-01-02-0304.tar.gz"
    assert set(res["entries"]) >= {"jobscout.db", "config.yaml", "MANIFEST.txt"}


def test_the_archive_contains_a_working_database(tmp_path):
    root = make_workspace(tmp_path / "ws", postings=7)
    res = bk.backup(root, tmp_path / "out")

    dest = tmp_path / "extract"
    dest.mkdir()
    with tarfile.open(res["archive"], "r:gz") as tar:
        tar.extractall(dest, filter="data")

    conn = sqlite3.connect(str(dest / "jobscout.db"))
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 7
    assert conn.execute("SELECT COUNT(*) FROM postings WHERE status='dismissed'").fetchone()[0] == 1
    conn.close()


def test_the_archive_contains_the_config_with_its_comments(tmp_path):
    root = make_workspace(tmp_path / "ws")
    res = bk.backup(root, tmp_path / "out")
    dest = tmp_path / "extract"
    dest.mkdir()
    with tarfile.open(res["archive"], "r:gz") as tar:
        tar.extractall(dest, filter="data")

    text = (dest / "config.yaml").read_text(encoding="utf-8")
    assert text == CONFIG                       # byte-identical, comments and all
    assert yaml.safe_load(text)["preferences"]["min_score"] == 6


def test_reports_are_included_and_can_be_left_out(tmp_path):
    root = make_workspace(tmp_path / "ws", reports=2)

    res = bk.backup(root, tmp_path / "out")
    assert sum(1 for n in res["entries"] if n.startswith("reports/")) == 2

    res2 = bk.backup(root, tmp_path / "out2", include_reports=False, now=datetime(2026, 2, 2))
    assert not [n for n in res2["entries"] if n.startswith("reports/")]


def test_manifest_describes_what_was_saved(tmp_path):
    root = make_workspace(tmp_path / "ws", postings=3)
    res = bk.backup(root, tmp_path / "out")
    dest = tmp_path / "extract"
    dest.mkdir()
    with tarfile.open(res["archive"], "r:gz") as tar:
        tar.extractall(dest, filter="data")

    mf = (dest / "MANIFEST.txt").read_text(encoding="utf-8")
    assert "postings  3" in mf
    assert "last run  #1" in mf
    assert "marked    1" in mf                  # tells the user their triage is in there
    assert "tar xzf" in mf                      # the restore command is right there


# --- the destination rule that exists because of the uninstaller -----------

def test_default_destination_is_OUTSIDE_the_workspace():
    """A deep uninstaller deletes ~/.jobscout. A backup stored inside it would
    be deleted by the same tool, so the default must be a sibling."""
    dest = bk.default_destination()
    ws = Path.home() / ".jobscout"
    assert dest != ws
    assert ws not in dest.parents
    assert dest.name == "jobscout-backups"


def test_backing_up_into_a_nested_dir_does_not_recurse(tmp_path):
    """Belt and braces for the same concern: even if someone points --to inside
    the workspace, the archive must not contain a previous backup."""
    root = make_workspace(tmp_path / "ws")
    inside = root / "backups"
    bk.backup(root, inside)
    res2 = bk.backup(root, inside, now=datetime(2026, 5, 5))
    assert not [n for n in res2["entries"] if n.endswith(".tar.gz")]


# --- failure must be loud --------------------------------------------------

def test_missing_database_raises_instead_of_producing_an_empty_archive(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "config.yaml").write_text(CONFIG, encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="no database"):
        bk.backup(root, tmp_path / "out")


def test_missing_config_raises(tmp_path):
    root = make_workspace(tmp_path / "ws")
    (root / "config.yaml").unlink()
    with pytest.raises(FileNotFoundError, match="no config"):
        bk.backup(root, tmp_path / "out")


def test_a_corrupt_source_is_not_silently_archived(tmp_path):
    """If the database is broken, say so rather than shipping a broken backup."""
    root = make_workspace(tmp_path / "ws")
    with open(root / "jobscout.db", "r+b") as f:      # scribble over page 1
        f.seek(0)
        f.write(b"\x00" * 4096)
    with pytest.raises(Exception):
        bk.backup(root, tmp_path / "out")


# --- the reason for the sqlite backup API ----------------------------------

def test_backup_is_consistent_while_another_connection_is_writing(tmp_path):
    """The desktop app can hold the database open while this runs. A byte copy
    taken mid-write can capture a torn page; Connection.backup cannot."""
    root = make_workspace(tmp_path / "ws", postings=1)
    db = root / "jobscout.db"
    stop = threading.Event()
    errors = []

    def hammer():
        conn = sqlite3.connect(str(db), timeout=10)
        try:
            i = 0
            while not stop.is_set():
                i += 1
                conn.execute("INSERT INTO postings (title, score, status) VALUES (?,?,?)",
                             (f"w{i}", 1.0, "new"))
                conn.commit()
        except Exception as e:                                  # noqa: BLE001
            errors.append(e)
        finally:
            conn.close()

    t = threading.Thread(target=hammer)
    t.start()
    archives = []
    try:
        for _ in range(3):
            archives.append(bk.backup(root, tmp_path / "out"))
            time.sleep(0.05)
    finally:
        stop.set()
        t.join(timeout=10)

    assert not errors, errors
    # every snapshot taken during the writes must still be a valid database
    assert len({a["archive"] for a in archives}) == 3, "each run must be its own file"
    for i, res in enumerate(archives):
        dest = tmp_path / f"x{i}"
        dest.mkdir()
        with tarfile.open(res["archive"], "r:gz") as tar:
            tar.extractall(dest, filter="data")
        conn = sqlite3.connect(str(dest / "jobscout.db"))
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()


# --- listing ---------------------------------------------------------------

def test_list_backups_newest_first_and_empty_when_absent(tmp_path):
    assert bk.list_backups(tmp_path / "nope") == []

    root = make_workspace(tmp_path / "ws")
    bk.backup(root, tmp_path / "out", now=datetime(2026, 1, 1, 10, 0))
    bk.backup(root, tmp_path / "out", now=datetime(2026, 3, 3, 10, 0))

    rows = bk.list_backups(tmp_path / "out")
    assert len(rows) == 2
    assert rows[0]["path"].name == "jobscout-2026-03-03-1000.tar.gz"
    assert rows[0]["bytes"] > 0


def test_a_second_backup_in_the_same_minute_never_replaces_the_first(tmp_path):
    """The whole job of a backup command is not losing things, so a name
    collision must produce a second file rather than silently overwriting the
    first restore point."""
    root = make_workspace(tmp_path / "ws", postings=4)
    when = datetime(2026, 1, 1, 12, 0)
    a = bk.backup(root, tmp_path / "out", now=when)
    b = bk.backup(root, tmp_path / "out", now=when)
    c = bk.backup(root, tmp_path / "out", now=when)

    names = sorted(p.name for p in (tmp_path / "out").glob("*.tar.gz"))
    assert names == ["jobscout-2026-01-01-1200-2.tar.gz",
                     "jobscout-2026-01-01-1200-3.tar.gz",
                     "jobscout-2026-01-01-1200.tar.gz"]
    for res in (a, b, c):
        with tarfile.open(res["archive"], "r:gz") as tar:
            assert tar.getnames()
        assert res["counts"]["postings"] == 4


# --- restore ---------------------------------------------------------------

def test_workspace_is_empty_only_when_nothing_is_there(tmp_path):
    ws = tmp_path / "ws"
    assert bk.workspace_is_empty(ws) is True        # does not even exist
    ws.mkdir()
    assert bk.workspace_is_empty(ws) is True
    (ws / "config.yaml").write_text("x")
    assert bk.workspace_is_empty(ws) is False
    (ws / "config.yaml").unlink()
    (ws / "jobscout.db").write_bytes(b"x")
    assert bk.workspace_is_empty(ws) is False


def test_restore_into_an_empty_workspace(tmp_path):
    src_ws = make_workspace(tmp_path / "src", postings=4)
    arc = bk.backup(src_ws, tmp_path / "out")["archive"]

    fresh = tmp_path / "fresh"
    res = bk.restore(arc, fresh)
    assert res["counts"]["postings"] == 4
    assert res["replaced_existing"] is False
    assert (fresh / "config.yaml").read_text() == CONFIG     # comments intact
    reports = list((fresh / "reports").glob("*.md"))
    assert len(reports) == 2


def test_restore_refuses_to_clobber_a_workspace(tmp_path):
    """Overwriting a workspace that holds decisions with an old snapshot is
    worse than leaving the backup unclaimed."""
    src_ws = make_workspace(tmp_path / "src")
    arc = bk.backup(src_ws, tmp_path / "out")["archive"]
    live = make_workspace(tmp_path / "live", postings=9)

    with pytest.raises(FileExistsError, match="already holds a workspace"):
        bk.restore(arc, live)
    # and the live workspace is untouched
    conn = sqlite3.connect(str(live / "jobscout.db"))
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 9
    conn.close()


def test_restore_keeps_a_SAFETY_COPY_of_what_it_replaces(tmp_path, monkeypatch):
    """The safety net must survive the call. An earlier version wrote it into a
    TemporaryDirectory deleted on the next line, so a restore you regret had no
    way back."""
    monkeypatch.setattr(bk, "default_destination", lambda: tmp_path / "safety")
    src_ws = make_workspace(tmp_path / "src", postings=3)
    arc = bk.backup(src_ws, tmp_path / "out")["archive"]
    live = make_workspace(tmp_path / "live", postings=7)

    res = bk.restore(arc, live, overwrite=True)
    safety = Path(res["safety_backup"])
    assert safety.exists(), "the safety copy must still be on disk"
    assert res["replaced_existing"] is True

    # and it really holds the pre-restore state
    dest = tmp_path / "extract"
    dest.mkdir()
    with tarfile.open(safety, "r:gz") as tar:
        tar.extractall(dest, filter="data")
    conn = sqlite3.connect(str(dest / "jobscout.db"))
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 7
    conn.close()


def test_restore_of_an_empty_workspace_needs_no_safety_copy(tmp_path):
    src_ws = make_workspace(tmp_path / "src")
    arc = bk.backup(src_ws, tmp_path / "out")["archive"]
    res = bk.restore(arc, tmp_path / "empty")
    assert res["safety_backup"] is None


def test_restore_refuses_something_that_is_not_a_backup(tmp_path):
    arc = tmp_path / "random.tar.gz"
    with tarfile.open(arc, "w:gz") as t:
        t.add(tmp_path, arcname="stuff")
    with pytest.raises(ValueError, match="not a jobscout backup"):
        bk.restore(arc, tmp_path / "ws")


def test_restore_refuses_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        bk.restore(tmp_path / "nope.tar.gz", tmp_path / "ws")


# --- describe (the manifest reader) ----------------------------------------

def test_describe_reports_what_is_inside(tmp_path):
    ws = make_workspace(tmp_path / "ws", postings=6)
    arc = bk.backup(ws, tmp_path / "out")["archive"]
    d = bk.describe(arc)
    assert d["postings"] == 6
    assert d["marked"] == 1
    assert d["created"]


def test_describe_reads_the_padded_marked_field(tmp_path):
    """`marked    84 (saved/applied/dismissed)` is space-padded. Splitting
    before stripping yields "" and reports 0 marked, which is a lie about the
    most valuable thing in the archive."""
    ws = make_workspace(tmp_path / "ws", postings=3)
    arc = bk.backup(ws, tmp_path / "out")["archive"]
    assert bk.describe(arc)["marked"] == 1
    assert isinstance(bk.describe(arc)["marked"], int)


def test_describe_survives_a_damaged_archive(tmp_path):
    bad = tmp_path / "jobscout-2026-01-01-0000.tar.gz"
    bad.write_bytes(b"not a tarball")
    d = bk.describe(bad)                       # must not raise
    assert d["postings"] is None
