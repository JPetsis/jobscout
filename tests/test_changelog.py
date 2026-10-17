"""The release-notes generator.

Worth testing because its failure mode is silent: an empty or duplicated
changelog still publishes, it just misinforms whoever reads the release.

Loaded straight from its path and called in-process. It was a bash script
before, invoked through subprocess, and on Windows those tests were really
exercising MSYS quoting and line endings rather than the notes.
"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"

_spec = importlib.util.spec_from_file_location("jobscout_changelog",
                                               PACKAGING / "changelog.py")
assert _spec and _spec.loader, f"cannot load {PACKAGING / 'changelog.py'}"
changelog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(changelog)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit(repo: Path, subject: str) -> None:
    f = repo / "f.txt"
    f.write_text(f.read_text(encoding="utf-8") + subject + "\n", encoding="utf-8")
    git(repo, "add", "f.txt")
    git(repo, "commit", "-m", subject)


@pytest.fixture()
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "T")
    (tmp_path / "f.txt").write_text("", encoding="utf-8")
    git(tmp_path, "add", "f.txt")
    git(tmp_path, "commit", "-m", "chore: initial")
    return tmp_path


def notes(repo: Path, *args: str) -> str:
    """What the CLI would print, through the same entry the workflow uses."""
    prev = changelog.previous_tag(args[0], cwd=str(repo)) if len(args) == 1 else args[0]
    return changelog.build_notes(prev, args[-1], cwd=str(repo))


def test_groups_by_conventional_prefix(repo):
    git(repo, "tag", "v0.1.0")
    commit(repo, "feat(app): a feature")
    commit(repo, "fix(db): a fix")
    commit(repo, "docs: a doc")
    commit(repo, "chore: housekeeping")
    git(repo, "tag", "v0.2.0")

    out = notes(repo, "v0.2.0")

    assert "## What changed since `v0.1.0`" in out
    assert "### Features" in out and "- a feature" in out
    assert "### Fixes" in out and "- a fix" in out
    assert "### Documentation" in out and "- a doc" in out
    assert "### Other" in out and "- housekeeping" in out
    # the scope is dropped, the human part kept
    assert "feat(app)" not in out


def test_an_unprefixed_subject_is_kept_not_dropped(repo):
    git(repo, "tag", "v0.1.0")
    commit(repo, "wireless failure")
    git(repo, "tag", "v0.2.0")

    assert "- wireless failure" in notes(repo, "v0.2.0"), \
        "a change must never vanish from its own notes"


def test_an_unfamiliar_prefix_keeps_its_whole_subject(repo):
    git(repo, "tag", "v0.1.0")
    commit(repo, "perf(scan): faster")
    commit(repo, "spike: tried the other way")
    git(repo, "tag", "v0.2.0")

    out = notes(repo, "v0.2.0")

    assert "### Performance" in out and "- faster" in out
    assert "- spike: tried the other way" in out


def test_a_release_commit_is_left_out(repo):
    """`release: 0.2.2` tells a reader of the notes nothing."""
    git(repo, "tag", "v0.1.0")
    commit(repo, "feat: a thing")
    commit(repo, "release: 0.2.2")
    git(repo, "tag", "v0.2.2")

    out = notes(repo, "v0.2.2")

    assert "- a thing" in out
    assert "- 0.2.2" not in out


def test_each_change_appears_exactly_once(repo):
    """The bug this test exists for: `grep -E "^chore|ci|test"` means `^chore` OR
    `ci` OR `test`, so subjects merely CONTAINING "ci" or "build" were listed
    twice, under their own heading and under Other."""
    git(repo, "tag", "v0.1.0")
    commit(repo, "fix(tests): compare as path components")
    commit(repo, "docs: the source table said 32, in a build that has 47")
    commit(repo, "feat: myScience boards")
    git(repo, "tag", "v0.2.0")

    out = notes(repo, "v0.2.0")

    for subject in ("compare as path components",
                    "the source table said 32, in a build that has 47",
                    "myScience boards"):
        assert out.count(f"- {subject}") == 1, f"listed twice: {subject}"
    assert "### Other" not in out, "none of these belong in Other"


def test_a_colon_that_is_not_a_prefix_is_not_a_prefix(repo):
    """'Deploy: roll back the cache' is not a `deploy` category."""
    git(repo, "tag", "v0.1.0")
    commit(repo, "Deploy: roll back the cache")
    git(repo, "tag", "v0.2.0")

    assert "- Deploy: roll back the cache" in notes(repo, "v0.2.0")


def test_no_previous_tag_says_what_changed(repo):
    commit(repo, "feat: first ever")
    git(repo, "tag", "v0.1.0")

    out = notes(repo, "v0.1.0")

    assert out.startswith("## What changed\n")
    assert "since" not in out.splitlines()[0], "there is no previous tag to name"
    assert "- first ever" in out


def test_nothing_between_the_tags_says_so(repo):
    git(repo, "tag", "v0.1.0")
    assert "No commits between the two tags" in notes(repo, "v0.1.0", "v0.1.0")


def test_the_previous_tag_is_found_from_the_tag_itself(repo):
    """The workflow passes only the new tag, so it must find the one before it --
    and an annotated tag has to work as well as a lightweight one."""
    git(repo, "tag", "v0.1.0")
    commit(repo, "feat: second wave")
    git(repo, "tag", "-a", "v0.2.0", "-m", "release 0.2.0")

    out = notes(repo, "v0.2.0")

    assert "since `v0.1.0`" in out
    assert "- second wave" in out


def test_the_cli_prints_exactly_what_the_function_returns(repo):
    """The workflow runs this as a script, so the two entries must agree."""
    git(repo, "tag", "v0.1.0")
    commit(repo, "feat: through the cli")
    git(repo, "tag", "v0.2.0")

    r = subprocess.run(
        ["python3", str(PACKAGING / "changelog.py"), "v0.1.0", "v0.2.0"],
        cwd=repo, capture_output=True, text=True)

    assert r.returncode == 0, r.stderr
    assert r.stdout == notes(repo, "v0.1.0", "v0.2.0")


def test_the_cli_refuses_a_missing_argument(repo):
    r = subprocess.run(["python3", str(PACKAGING / "changelog.py")],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 2
    assert "usage" in r.stderr.lower()


def test_no_shell_script_is_needed_for_a_release():
    """The bash version failed on Windows for reasons that had nothing to do
    with the notes (MSYS quoting, then CRLF from a Windows checkout). Python
    removes the whole class, and this keeps it removed."""
    leftovers = sorted(p.name for p in PACKAGING.glob("*.sh"))
    assert leftovers == [], f"a shell script is back: {leftovers}"
