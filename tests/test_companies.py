import json

import yaml

from jobscout import companies
from jobscout.config import DEFAULT_CONFIG
from jobscout.http import FetchError


class FakeFetcher:
    def __init__(self, response):
        self.response = response

    def get(self, url):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _load(text):
    return yaml.safe_load(text)["companies"]


def test_detect_from_urls():
    assert ("greenhouse", "acme") in companies.detect("", "https://boards.greenhouse.io/acme")
    assert ("greenhouse", "acme2") in companies.detect("", "https://job-boards.greenhouse.io/acme2/jobs/1")
    assert ("ashby", "n8n") in companies.detect("", "https://jobs.ashbyhq.com/n8n/abc")
    assert ("lever", "leverdemo") in companies.detect("", "https://jobs.lever.co/leverdemo")
    assert ("smartrecruiters", "visa") in companies.detect("", "https://jobs.smartrecruiters.com/visa/1")
    assert companies.detect("", "https://example.com/careers") == []


def test_detect_prefers_urls_then_page_text():
    html = '<script src="https://jobs.ashbyhq.com/x"></script>'
    got = companies.detect(html, "https://boards.greenhouse.io/first")
    assert got[0] == ("greenhouse", "first")
    assert ("ashby", "x") in got


def test_validate_ok_counts_roles():
    fetcher = FakeFetcher((200, json.dumps({"jobs": [1, 2, 3]})))
    ok, detail = companies.validate(fetcher, "greenhouse", "acme")
    assert ok and "3 open role(s)" in detail
    ok2, detail2 = companies.validate(FakeFetcher((200, json.dumps([1, 2]))), "lever", "x")
    assert ok2 and "2 open role(s)" in detail2


def test_validate_failures():
    assert companies.validate(FakeFetcher((404, "")), "greenhouse", "x")[0] is False
    assert companies.validate(FakeFetcher((200, "<html>")), "ashby", "x") == \
        (False, "API answered, but not JSON")
    assert companies.validate(FakeFetcher(FetchError("https://x/1", "boom")), "ashby", "x") == \
        (False, "fetch error (boom)")


def test_inject_into_existing_inline_list():
    text = "companies:\n  greenhouse:\n    tokens: [acme, other]\n  ashby:\n    tokens: []\n"
    new, status = companies._inject(text, "greenhouse", "newco")
    assert status == "added"
    assert _load(new)["greenhouse"]["tokens"] == ["acme", "other", "newco"]


def test_inject_existing_token_is_a_noop():
    text = "companies:\n  ashby:\n    tokens: [n8n]\n"
    new, status = companies._inject(text, "ashby", "n8n")
    assert status == "exists" and new == text


def test_inject_empty_list_and_missing_section():
    text = "companies:\n  ashby:\n    tokens: []\n  lever:\n    tokens: [d]\n"
    new, status = companies._inject(text, "ashby", "fresh")
    assert status == "added" and _load(new)["ashby"]["tokens"] == ["fresh"]
    new2, status2 = companies._inject(text, "workable", "demo")
    assert status2 == "section-created"
    assert _load(new2)["workable"]["tokens"] == ["demo"]
    assert _load(new2)["lever"]["tokens"] == ["d"]  # neighbours untouched


def test_inject_creates_companies_block_when_absent():
    text = "sources:\n  enabled: [hn]\n"
    new, status = companies._inject(text, "ashby", "x")
    assert status == "section-created"
    assert _load(new)["ashby"]["tokens"] == ["x"]


def test_inject_into_block_list():
    text = ("companies:\n  lever:\n    tokens:\n      - a\n      - b\n"
            "  ashby:\n    tokens: [x]\n")
    new, status = companies._inject(text, "lever", "c")
    assert status == "added"
    assert _load(new)["lever"]["tokens"] == ["a", "b", "c"]
    assert _load(new)["ashby"]["tokens"] == ["x"]


def test_inject_works_on_the_real_template():
    new, status = companies._inject(DEFAULT_CONFIG, "lever", "acme-lever")
    assert status in ("added", "section-created")
    data = _load(new)
    assert "acme-lever" in data["lever"]["tokens"]
    assert "greenhouse" in data and data["greenhouse"]["tokens"]  # nothing else disturbed

def test_enable_source_appends_to_multiline_list():
    text = ("sources:\n  # sources run in this order\n  enabled: [linkedin, hn,\n"
            "            greenhouse, ashby]\n")
    new, status = companies._enable_in_list(text, "lever")
    assert status == "added"
    assert new == ("sources:\n  # sources run in this order\n  enabled: [linkedin, hn,\n"
                   "            greenhouse, ashby, lever]\n")
    assert companies._enable_in_list(new, "lever") == (new, "exists")


def test_enable_source_missing_list():
    assert companies._enable_in_list("sources:\n  other: 1\n", "lever")[1] == "missing"


def test_enable_source_on_real_template():
    """Behavioural, not a literal slice of the template: the old assertion
    pinned the exact text `...recruitee, lever]` and broke the moment a source
    was added to the default list, which is not a regression."""
    before = yaml.safe_load(DEFAULT_CONFIG)["sources"]["enabled"]
    new, status = companies._enable_in_list(DEFAULT_CONFIG, "lever")
    assert status == "added"
    after = yaml.safe_load(new)["sources"]["enabled"]
    assert after[-1] == "lever"                 # appended
    assert after[:-1] == before                 # nothing else disturbed
    assert DEFAULT_CONFIG.count("#") == new.count("#")   # comments untouched
