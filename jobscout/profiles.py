"""Profiles — several independent workspaces on one machine.

A profile is a WHOLE workspace: its own `config.yaml`, its own database, its own
reports. It is not a filter over a shared pool, and that is the point:

  * a second person on the same computer gets a genuinely empty app, rather
    than the first person's postings filtered differently;
  * one person can keep two searches configured completely differently --
    different titles, different skills, different countries -- without either
    contaminating the other.

Storage:

    ~/.jobscout/                  the base, and the "Default" profile
      config.yaml
      jobscout.db
      active-profile              one line: the profile in use (absent = Default)
      profiles/
        archaeology/              a profile is a workspace like any other
          config.yaml
          jobscout.db

The base root does not move, so an existing install simply IS the Default
profile and nothing migrates.

`config.home()` resolves through here, which is why the CLI, the scorer, the
database and the window all pick up the active profile without any of them
knowing profiles exist.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .cfgwrite import write_atomic

BASE_ENV = "JOBSCOUT_HOME"
POINTER_NAME = "active-profile"
PROFILES_DIR = "profiles"
DEFAULT_LABEL = "Default"
MAX_NAME = 40

# A profile name becomes a DIRECTORY NAME, so this is deliberately strict:
# anything that could escape profiles/ (a separator, a leading dot, "..") is
# refused rather than sanitised, because a silently rewritten name is worse
# than a rejected one.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")


# ---------------------------------------------------------------- location

def base_root() -> Path:
    """The top-level ~/.jobscout (or $JOBSCOUT_HOME). Everything lives under it."""
    env = os.environ.get(BASE_ENV)
    return Path(env).expanduser() if env else Path.home() / ".jobscout"


def profiles_dir() -> Path:
    return base_root() / PROFILES_DIR


def pointer_path() -> Path:
    return base_root() / POINTER_NAME


def profile_root(name: str) -> Path:
    """Where a named profile lives. "" is the base workspace itself."""
    return profiles_dir() / name if name else base_root()


def resolve_root() -> Path:
    """The workspace in use.

    Falls back to the base when the pointer names a profile that is not there,
    so deleting a directory by hand cannot leave the app unable to start.
    """
    return profile_root(read_active())


# ---------------------------------------------------------------- names

def name_problem(name: str) -> str | None:
    """Why this name cannot be used, or None. Shown to the user verbatim."""
    if not name:
        return "a profile needs a name"
    if len(name) > MAX_NAME:
        return f"that name is too long ({MAX_NAME} characters at most)"
    if not _NAME_RE.match(name):
        return ("use letters, numbers, spaces, dashes and underscores, "
                "starting with a letter or number")
    if name.lower() == DEFAULT_LABEL.lower():
        return f"'{DEFAULT_LABEL}' is the name of your existing profile"
    return None


def valid_name(name: str) -> bool:
    return name_problem(name) is None


# ---------------------------------------------------------------- the pointer

def _pointer_value() -> str:
    """What the pointer file literally says, with no checking."""
    try:
        return pointer_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_active() -> str:
    """The profile actually in use, or "" when the base workspace is.

    Deliberately reports the EFFECTIVE profile, not the raw pointer: if the
    pointer names a directory that is no longer there, the app runs on the base,
    and saying otherwise would leave the window highlighting a profile that
    nothing is actually reading from.
    """
    name = _pointer_value()
    if not name or not valid_name(name):
        return ""
    return name if profile_root(name).is_dir() else ""


def write_active(name: str) -> None:
    """Point the app at a profile. "" selects the base ("Default")."""
    if name and not valid_name(name):
        raise ValueError(name_problem(name) or "invalid profile name")
    path = pointer_path()
    if not name:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, name + "\n")


# ---------------------------------------------------------------- listing

def _postings(root: Path) -> int | None:
    """How much is in this profile, or None if that cannot be read.

    Only ever cosmetic -- a profile whose database is missing or locked still
    lists, because refusing to show a profile the user made would be worse than
    showing one without a number next to it.
    """
    import sqlite3
    db = root / "jobscout.db"
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0])
        finally:
            conn.close()
    except (sqlite3.Error, OSError, TypeError):
        return None


def list_profiles() -> list[dict]:
    """Every profile, Default first, then the rest alphabetically.

    `name` is the key to pass back ("" for Default); `label` is what to show.
    """
    active = read_active()
    base = base_root()
    rows = [{
        "name": "",
        "label": DEFAULT_LABEL,
        "root": str(base),
        "active": active == "",
        "exists": True,
        "postings": _postings(base),
    }]
    pdir = profiles_dir()
    names: list[str] = []
    if pdir.is_dir():
        try:
            names = sorted((d.name for d in pdir.iterdir()
                            if d.is_dir() and valid_name(d.name)),
                           key=str.lower)
        except OSError:
            names = []
    for name in names:
        root = pdir / name
        rows.append({
            "name": name,
            "label": name,
            "root": str(root),
            "active": active == name,
            "exists": True,
            "postings": _postings(root),
        })
    return rows


def find(name: str) -> dict | None:
    for row in list_profiles():
        if row["name"] == name:
            return row
    return None


# ---------------------------------------------------------------- lifecycle

def create(name: str) -> Path:
    """Make a NEW, empty profile. Raises ValueError on a bad or taken name.

    Empty is the whole point: it starts from the template, so a new user in
    another field never inherits a software-specific profile, and it has no
    database until its first scan.
    """
    problem = name_problem(name)
    if problem:
        raise ValueError(problem)
    root = profile_root(name)
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"a profile called '{name}' already exists")
    # Refuse a name that only differs in CASE from an existing one. macOS and
    # Windows would collide on the directory anyway; Linux would happily make a
    # second profile whose name looks identical in the picker, which is worse
    # than refusing. Checked here so the behaviour does not depend on the
    # filesystem the app happens to be running on.
    for row in list_profiles():
        if row["name"] and row["name"].lower() == name.lower():
            raise ValueError(f"a profile called '{row['name']}' already exists")
    from .config import init_workspace
    root.mkdir(parents=True, exist_ok=True)
    init_workspace(root)
    return root


def remove(name: str, *, dest_dir: Path | None = None) -> Path:
    """Delete a profile, but never without a verified backup first.

    Refuses the Default profile: it is not a directory under profiles/, it is
    the base workspace, and deleting it would mean deleting everything.
    Returns the archive written before the deletion.
    """
    from . import backup as backupmod
    if not name:
        raise ValueError(f"{DEFAULT_LABEL} cannot be removed — it is your main "
                         "workspace, not a profile")
    problem = name_problem(name)
    if problem:
        raise ValueError(problem)
    root = profile_root(name)
    if not root.is_dir():
        raise ValueError(f"no profile called '{name}'")
    dest = dest_dir or backupmod.default_destination()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "profile"
    archive = backupmod.backup(root, dest, include_reports=True,
                               prefix=f"profile-{slug}")["archive"]
    # Only now is deleting defensible: a backup that failed raised above, so
    # there is no path here that destroys data without a copy of it existing.
    shutil.rmtree(root)
    if read_active() == name:
        write_active("")
    return Path(archive)
