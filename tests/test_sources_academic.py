"""The 2026-10-15 academic batch: academics, wissenschaftskarriere, euraxess,
myscience.

Fixtures are real slices captured live on 2026-10-15; fetch() behaviour is
exercised against scripted HTTP responses (same pattern as
test_sources_expansion.py).
"""
from types import SimpleNamespace

import yaml

from jobscout import querygen
from jobscout.config import DEFAULT_CONFIG, SOURCE_DEFAULTS
from jobscout.sources import (REGISTRY, SOURCE_NAMES, academics, euraxess,
                              myscience, wissenschaftskarriere)

NEW_SOURCES = ["academics", "wissenschaftskarriere", "euraxess", "myscience"]


class FakeGet:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.errors = []

    def get(self, url, headers=None, host_delay=None):
        self.urls.append(url)
        return self.responses.pop(0) if self.responses else (404, "")


def test_batch_registered():
    for name in NEW_SOURCES:
        assert name in SOURCE_NAMES
        assert REGISTRY[name].NAME == name
        assert callable(REGISTRY[name].fetch)
        assert name in SOURCE_DEFAULTS


def test_template_carries_the_batch():
    d = yaml.safe_load(DEFAULT_CONFIG)
    for name in NEW_SOURCES:
        assert name in d["sources"]["enabled"], name
        assert name in d["sources"], name


# ------------------------------------------------------------- academics ---

def test_academics_parse(fixture_text):
    ps = academics.parse(fixture_text("academics_list.html"))
    assert len(ps) >= 2
    p = ps[0]
    assert p.title.startswith("Professur (m/w/d) für das Fachgebiet")
    assert p.company == "Hochschule Kehl - Hochschule für öffentliche Verwaltung"
    assert p.location == "Kehl"
    assert p.url.startswith("https://www.academics.de/jobs/")
    assert p.external_id == "1111819"
    assert p.posted_at == "2026-10-15"
    assert p.description == "Arbeitszeit: Vollzeit · Befristung: Unbefristet"


def test_academics_fetch_pages(fixture_text):
    html = fixture_text("academics_list.html")
    f = FakeGet([(200, html), (200, html)])
    ps = academics.fetch({"pages": 2}, f)
    assert f.urls[0] == "https://www.academics.de/stellenanzeigen"
    assert f.urls[1].endswith("?page=2")
    assert len(ps) == 2 * len(academics.parse(html))


def test_academics_fetch_error():
    f = FakeGet([(503, "")])
    assert academics.fetch({"pages": 2}, f) == []
    assert any("academics" in e for e in f.errors)


# ------------------------------------------------- wissenschaftskarriere ---

def test_duz_parse(fixture_text):
    ps = wissenschaftskarriere.parse(fixture_text("duz_list.html"))
    assert len(ps) >= 2
    for p in ps:
        assert p.title
        assert p.url.startswith("https://wissenschaftskarriere.duz.de/job/")
        assert p.external_id.isdigit()
    assert any(p.company for p in ps)
    assert any(p.location for p in ps)


def test_duz_fetch_single_page(fixture_text):
    html = fixture_text("duz_list.html")
    f = FakeGet([(200, html)])
    ps = wissenschaftskarriere.fetch({}, f)
    assert f.urls == ["https://wissenschaftskarriere.duz.de/suchergebnis.html"]
    assert len(ps) == len(wissenschaftskarriere.parse(html))
    f2 = FakeGet([(500, "")])
    assert wissenschaftskarriere.fetch({}, f2) == []
    assert f2.errors


# ---------------------------------------------------------------- euraxess --

def test_euraxess_parse(fixture_text):
    ps = euraxess.parse(fixture_text("euraxess_list.html"))
    assert len(ps) >= 3
    p = ps[0]
    assert p.company == "University of Ostrava"
    assert p.title.startswith("Professor / Associate Professor in Nursing")
    assert p.url == "https://euraxess.ec.europa.eu/jobs/473111"
    assert p.posted_at == "2026-10-15"
    assert "Dean of the Faculty" in p.description


def test_euraxess_paging_is_zero_based(fixture_text):
    html = fixture_text("euraxess_list.html")
    f = FakeGet([(200, html), (200, html)])
    euraxess.fetch({"pages": 2}, f)
    assert f.urls == ["https://euraxess.ec.europa.eu/jobs/search",
                      "https://euraxess.ec.europa.eu/jobs/search?page=1"]


# --------------------------------------------------------------- myscience --

def test_myscience_parse(fixture_text):
    ps = myscience.parse(fixture_text("myscience_list.html"))
    assert len(ps) >= 2
    p = ps[0]
    assert p.title.startswith("Assistant Professor (Tenure Track)")
    assert p.company == "ETH Zürich"
    assert p.location == "Zürich, Schweiz"
    assert p.url.startswith("https://www.myscience.de/jobs/id216707-")
    assert p.external_id == "216707"
    assert p.posted_at == "2026-10-14"
    assert "ETH Zurich invites" in p.description


def test_myscience_detail_reads_the_body_region(fixture_text):
    """The generic extractor only sees the ~330-char meta blurb; the advert
    lives in the body's itemprop="description" region. Regression for the
    DevOps row, whose rescore lost its PHP chip to the blurb."""
    ps = myscience.parse(fixture_text("myscience_list.html"))
    f = FakeGet([(200, fixture_text("myscience_detail.html"))])
    desc = myscience.fetch_detail(ps[0], f)
    assert desc and len(desc) > 1000
    assert "Das sind Ihre Aufgaben" in desc
    assert "PHP" in desc


def test_myscience_query_and_location_warning(fixture_text):
    html = fixture_text("myscience_list.html")
    f = FakeGet([(200, html)])
    myscience.fetch({"queries": [{"keywords": "professur physics", "location": "Bern"}]}, f)
    assert f.urls == ["https://www.myscience.de/jobs?q=professur+physics"]
    assert any("location filter not supported" in e for e in f.errors)
    f2 = FakeGet([(200, html)])
    myscience.fetch({"queries": [{"keywords": ""}]}, f2)
    assert f2.urls == ["https://www.myscience.de/jobs"]


def test_myscience_is_query_driven_for_querygen():
    cfg = SimpleNamespace(sources_cfg={}, prefs={})
    prefs = {"title_terms": ["professor"], "query_budget": 6,
             "stack_preferred": {}, "stack_terms": []}
    qs = querygen.generate_queries(cfg, "myscience", prefs)
    assert [q["keywords"] for q in qs][:2] == ["", "professor"]
    # the walk-only boards are not query-driven
    assert querygen.generate_queries(cfg, "academics", prefs) == []
    assert querygen.generate_queries(cfg, "euraxess", prefs) == []
