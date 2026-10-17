"""The release-notes generator.

Worth testing because its failure mode is silent: an empty or duplicated
changelog still publishes, it just misinforms whoever reads the release.
"""
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "changelog.sh"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit(repo: Path, subject: str) -> None:
    f = repo / "f.txt"
    f.write_text(f.read_text() + subject + "\n", encoding="utf-8")
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


def run(repo: Path, *args: str) -> str:
    r = subprocess.run(["bash", str(SCRIPT), *args], cwd=repo,
                       capture_output=True, text=True)
    # Not check=True: on a CRLF checkout bash dies on line 1 with a message
    # about `pipefail\r`, and a bare CalledProcessError hides exactly the line
    # that explains it.
    assert r.returncode == 0, f"changelog.sh failed ({r.returncode}):\n{r.stderr}"
    return r.stdout


def test_groups_by_conventional_prefix(repo):
    git(repo, "tag", "v0.1.0")
    commit(repo, "feat(app): a feature")
    commit(repo, "fix(db): a fix")
    commit(repo, "docs: a doc")
    commit(repo, "chore: housekeeping")
    git(repo, "tag", "v0.2.0")

    out = run(repo, "v0.2.0")

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

    out = run(repo, "v0.2.0")

    assert "- wireless failure" in out, "a change must never vanish from its own notes"


def test_each_change_appears_exactly_once(repo):
    """The bug this test exists for: `^chore|ci|test` means `^chore` OR `ci`
    OR `test`, so subjects merely CONTAINING "ci" or "build" were listed twice,
    under their own heading and under Other."""
    git(repo, "tag", "v0.1.0")
    commit(repo, "fix(tests): compare as path components")
    commit(repo, "docs: the source table said 32, in a build that has 47")
    commit(repo, "feat: myScience boards")
    git(repo, "tag", "v0.2.0")

    out = run(repo, "v0.2.0")

    for subject in ("compare as path components",
                    "the source table said 32, in a build that has 47",
                    "myScience boards"):
        assert out.count(f"- {subject}") == 1, f"listed twice: {subject}"
    assert "### Other" not in out, "none of these belong in Other"


def test_no_previous_tag_says_what_changed(repo):
    commit(repo, "feat: first ever")
    git(repo, "tag", "v0.1.0")

    out = run(repo, "v0.1.0")

    assert "## What changed" in out
    assert "since" not in out.splitlines()[0], "there is no previous tag to name"
    assert "- first ever" in out


def test_nothing_between_the_tags_says_so(repo):
    git(repo, "tag", "v0.1.0")
    out = run(repo, "v0.1.0", "v0.1.0")
    assert "No commits between the two tags" in out


def test_the_previous_tag_is_found_from_the_tag_itself(repo):
    """The workflow passes only the new tag, so the script must find the one
    before it -- and an annotated tag has to work as well as a lightweight one."""
    git(repo, "tag", "v0.1.0")
    commit(repo, "feat: second wave")
    git(repo, "tag", "-a", "v0.2.0", "-m", "release 0.2.0")

    out = run(repo, "v0.2.0")

    assert "since `v0.1.0`" in out
    assert "- second wave" in out


def test_shell_scripts_are_lf_and_pinned_to_stay_that_way():
    """This is how the Windows job failed.

    Git for Windows defaults to `core.autocrlf=true`, so a checked-out `.sh` has
    CRLF and bash reads `set -euo pipefail` as `pipefail\\r: invalid option
    name`, exiting before it runs a single useful line. `.gitattributes` is the
    fix; this catches both a missing rule and a script committed with CRLF
    anyway.
    """
    repo = Path(__file__).resolve().parents[1]
    scripts = sorted(repo.glob("packaging/*.sh"))
    assert scripts, "no shell scripts found; did they move?"

    for s in scripts:
        assert b"\r\n" not in s.read_bytes(), f"{s.name} is committed with CRLF"

    attrs = (repo / ".gitattributes")
    assert attrs.exists(), "a Windows checkout would rewrite these as CRLF"
    text = attrs.read_text(encoding="utf-8")
    assert "*.sh" in text and "eol=lf" in text
