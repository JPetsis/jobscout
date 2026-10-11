import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def fixture_text():
    def _get(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")
    return _get


@pytest.fixture()
def fixture_json():
    def _get(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return _get


@pytest.fixture()
def strip_styles():
    import re

    def _strip(s: str) -> str:
        return re.sub(r"<style[^>]*>.*?</style>", "", s, flags=re.S)
    return _strip


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    """A fresh workspace in a temp dir, with the default config."""
    monkeypatch.setenv("JOBSCOUT_HOME", str(tmp_path / "jobscout-home"))
    from jobscout.config import home, init_workspace

    root = home()
    init_workspace(root)
    return root
