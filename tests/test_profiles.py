"""Profiles: separate workspaces on one machine.

The tests are written against what each behaviour prevents. The two that
matter most:

  * a profile name becomes a DIRECTORY NAME, so anything that could escape
    profiles/ has to be refused, not sanitised;
  * removing a profile must never be able to destroy data, which means the
    backup happens first and its failure cancels the removal.
"""
import sqlite3
from pathlib import Path

import pytest

from jobscout import profiles


@pytest.fixture()
def base(tmp_path, monkeypatch):
    """A throwaway base root, so nothing here touches ~/.jobscout."""
    monkeypatch.setenv("JOBSCOUT_HOME", str(tmp_path / "jobscout-home"))
    root = profiles.base_root()
    from jobscout.config import init_workspace
    init_workspace(root)
    return root


def _seed(root: Path, n: int) -> None:
    """A real workspace database with `n` postings in it."""
    from jobscout import db as dbm
    conn = dbm.connect(root / "jobscout.db")
    run = conn.execute("INSERT INTO runs (started_at) VALUES ('2026-01-01T00:00:00')")
    rid = run.lastrowid
    for i in range(n):
        conn.execute(
            "INSERT INTO postings (fingerprint, source, title, company, url, "
            "first_seen_at, first_seen_run, last_seen_at, last_seen_run, "
            "score, filtered, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,0,'new')",
            (f"fp{i}", "themuse", f"job {i}", "Acme", "https://x.invalid/1",
             "2026-01-01T00:00:00", rid, "2026-01-01T00:00:00", rid, 9.0))
    conn.commit()
    conn.close()


# --- location ---------------------------------------------------------------

def test_without_a_pointer_the_base_workspace_is_used(base):
    assert profiles.read_active() == ""
    assert profiles.resolve_root() == base


def test_the_pointer_moves_the_workspace(base):
    profiles.create("Archaeology")
    profiles.write_active("Archaeology")
    assert profiles.resolve_root() == base / "profiles" / "Archaeology"


def test_home_follows_the_active_profile(base):
    """The whole feature rests on this: one resolution point, so the CLI, the
    scorer and the window all pick the profile up without knowing it exists."""
    from jobscout.config import home
    assert home() == base
    profiles.create("Archaeology")
    profiles.write_active("Archaeology")
    assert home() == base / "profiles" / "Archaeology"


def test_a_stale_pointer_falls_back_to_the_base(base):
    """Deleting a profile folder by hand must not leave the app unable to start."""
    profiles.create("Gone")
    profiles.write_active("Gone")
    import shutil
    shutil.rmtree(profiles.profile_root("Gone"))
    assert profiles.resolve_root() == base
    assert profiles.read_active() == "", "a pointer to nothing is not 'active'"


def test_the_list_marks_what_is_actually_in_use(base):
    profiles.create("Gone")
    profiles.write_active("Gone")
    import shutil
    shutil.rmtree(profiles.profile_root("Gone"))
    active = [r["label"] for r in profiles.list_profiles() if r["active"]]
    assert active == ["Default"]


# --- names become directory names ------------------------------------------

@pytest.mark.parametrize("bad", [
    "", "   ", ".", "..", "../escape", "a/b", "a\\b", "/etc/passwd",
    ".hidden", "a" * 41, "Default", "default",
])
def test_dangerous_or_unusable_names_are_refused(bad):
    assert profiles.name_problem(bad) is not None
    with pytest.raises(ValueError):
        profiles.create(bad)


@pytest.mark.parametrize("good", [
    "Archaeology", "Freelance", "prof-2", "a_b", "My Profile", "1st",
])
def test_reasonable_names_are_accepted(good):
    assert profiles.name_problem(good) is None


def test_refusing_a_name_does_not_create_anything(base):
    with pytest.raises(ValueError):
        profiles.create("../escape")
    assert not (base / "profiles").exists() or not any((base / "profiles").iterdir())
    assert not (base.parent / "escape").exists()


def test_write_active_refuses_a_bad_name(base):
    with pytest.raises(ValueError):
        profiles.write_active("../escape")


# --- create -----------------------------------------------------------------

def test_a_new_profile_is_empty_and_fully_formed(base):
    root = profiles.create("Archaeology")
    assert (root / "config.yaml").is_file(), "it must be usable immediately"
    assert (root / "reports").is_dir()
    assert not (root / "jobscout.db").exists(), "empty means no postings at all"


def _terms_of(text: str):
    import yaml
    return (yaml.safe_load(text) or {})["preferences"]["title_terms"]


def test_a_new_profile_does_not_inherit_the_current_one(base):
    """A fresh profile starts from the shipped template. A second user in
    another field must never inherit a software-specific profile."""
    from jobscout import cfgwrite
    from jobscout.config import DEFAULT_CONFIG, load_config
    (base / "config.yaml").write_text(
        cfgwrite.set_list(load_config(base).path.read_text(encoding="utf-8"),
                          "preferences.title_terms", ["php developer"]),
        encoding="utf-8")
    root = profiles.create("Archaeology")
    fresh = load_config(root)
    assert fresh.prefs.get("title_terms") != ["php developer"]
    assert fresh.prefs.get("title_terms") == _terms_of(DEFAULT_CONFIG)


def test_a_duplicate_name_is_refused(base):
    profiles.create("Twice")
    with pytest.raises(ValueError, match="already exists"):
        profiles.create("Twice")


def test_creating_is_case_insensitively_unique_on_the_filesystem(base):
    """On a case-insensitive filesystem these are the same directory, and
    create() must not silently adopt the existing one."""
    profiles.create("Twice")
    with pytest.raises(ValueError):
        profiles.create("twice")


# --- listing ----------------------------------------------------------------

def test_the_list_shows_counts(base):
    _seed(base, 3)
    profiles.create("Empty")
    rows = {r["label"]: r for r in profiles.list_profiles()}
    assert rows["Default"]["postings"] == 3
    assert rows["Empty"]["postings"] is None, "no database yet"


def test_an_unreadable_database_still_lists(base):
    """Refusing to show a profile the user made would be worse than showing
    one without a number beside it."""
    root = profiles.create("Broken")
    (root / "jobscout.db").write_bytes(b"not a database")
    rows = {r["label"]: r for r in profiles.list_profiles()}
    assert "Broken" in rows
    assert rows["Broken"]["postings"] is None


def test_default_is_always_first(base):
    profiles.create("Zebra")
    profiles.create("Aardvark")
    assert [r["label"] for r in profiles.list_profiles()] == \
        ["Default", "Aardvark", "Zebra"]


def test_find_reports_absence(base):
    assert profiles.find("nope") is None
    assert profiles.find("")["label"] == "Default"


# --- remove -----------------------------------------------------------------

def test_removing_a_profile_takes_a_backup_first(base, tmp_path):
    root = profiles.create("Doomed")
    _seed(root, 2)
    archive = profiles.remove("Doomed", dest_dir=tmp_path / "backups")
    assert archive.is_file()
    assert archive.name.startswith("profile-doomed-"), \
        "the archive name has to say which profile it holds"
    import tarfile
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "jobscout.db" in names and "config.yaml" in names


def test_the_profile_is_actually_gone_after_removal(base, tmp_path):
    root = profiles.create("Doomed")
    _seed(root, 1)
    profiles.remove("Doomed", dest_dir=tmp_path / "backups")
    assert not root.exists()


def test_removal_is_cancelled_when_the_backup_fails(base, tmp_path, monkeypatch):
    """The whole safety story: no backup, no deletion."""
    root = profiles.create("Doomed")
    _seed(root, 1)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("jobscout.backup.backup", boom)
    with pytest.raises(OSError):
        profiles.remove("Doomed", dest_dir=tmp_path / "backups")
    assert root.exists(), "a failed backup must leave the profile alone"


def test_the_default_profile_cannot_be_removed(base):
    with pytest.raises(ValueError, match="cannot be removed"):
        profiles.remove("")


def test_removing_the_active_profile_returns_to_default(base, tmp_path):
    profiles.create("Current")
    _seed(profiles.profile_root("Current"), 1)
    profiles.write_active("Current")
    profiles.remove("Current", dest_dir=tmp_path / "backups")
    assert profiles.read_active() == ""
    assert profiles.resolve_root() == base


def test_removing_something_that_is_not_there_is_refused(base, tmp_path):
    with pytest.raises(ValueError, match="no profile called"):
        profiles.remove("ghost", dest_dir=tmp_path / "backups")
