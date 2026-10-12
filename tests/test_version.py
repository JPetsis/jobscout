"""The version has to agree with itself, and with what the updater compares.

Three places matter and drift between them is silent:
  * `jobscout.__version__` -- what the running app reports, and what the update
    check compares a release tag against;
  * `pyproject.toml` -- what PyInstaller bakes into the bundle and what the
    package metadata says;
  * the git tag on a release -- what the update check actually sees.

A mismatch between the first two means the frozen app calls itself one version
while the package says another, so an update is offered forever or never.
"""
import re
import tomllib
from pathlib import Path

import jobscout

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def test_version_matches_pyproject():
    pyproject_version = tomllib.loads(
        PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert jobscout.__version__ == pyproject_version, (
        "jobscout.__version__ and pyproject.toml disagree; the update check "
        "compares against the former and the bundle is built with the latter")


def test_version_is_a_plain_semver_triple():
    parts = jobscout.__version__.split(".")
    assert len(parts) == 3, jobscout.__version__
    assert all(p.isdigit() for p in parts), jobscout.__version__


def test_the_updater_can_compare_our_own_version():
    from jobscout.update import is_newer, parse_version
    v = jobscout.__version__
    assert parse_version(v)                       # parses at all
    assert not is_newer(f"v{v}", v)               # is not newer than itself
    major, minor, patch = (int(p) for p in v.split("."))
    assert is_newer(f"v{major}.{minor}.{patch + 1}", v)


def test_the_spec_reads_the_same_version_the_package_reports():
    """The PyInstaller spec reads pyproject.toml, so the bundle's reported
    version comes from there. If the two ever diverge, a frozen app would
    misreport itself to the updater."""
    spec = (ROOT / "packaging" / "jobscout.spec").read_text(encoding="utf-8")
    assert "pyproject.toml" in spec, "the spec should read the version from pyproject"
    assert re.search(r'\["project"\]\["version"\]', spec)
