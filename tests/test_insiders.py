"""The "fresh from the source" mark: where a posting comes from, and how young.

The claim the window makes, in one sentence, is: this posting is on a channel
the employer publishes into themselves, it is recent, and no other board we poll
has carried it yet. Every test here guards one of those three words, because
each is separately falsifiable and the whole mark is worthless if any of them is
a guess.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from jobscout import api, db as dbm
from jobscout import sources
from jobscout.models import Posting
from jobscout.score import score_posting

PREFS = {"title_terms": ["entwickler"], "title_boost": 3,
         "stack_terms": ["php"], "stack_boost": 2, "stack_cap": 6,
         "keywords": [], "exclude_terms": [], "title_deny": []}


# --- the tier table --------------------------------------------------------

def test_every_source_is_classified():
    """A new board must be classified on purpose, not silently.

    Not being direct is the safe default (no badge, no claim), so this is not
    about behaviour: it is about the decision being made rather than inherited
    from whatever happens to be in the dict.
    """
    direct = set(sources.DIRECT_SOURCES)
    listed = set(sources.NOT_DIRECT)
    assert not (direct & listed), f"classified both ways: {sorted(direct & listed)}"
    missing = sorted(set(sources.SOURCE_NAMES) - direct - listed)
    assert not missing, ("these sources are neither direct nor deliberately not "
                         f"direct: {missing}")
    extra = sorted((direct | listed) - set(sources.SOURCE_NAMES))
    assert not extra, f"classified but not in the registry: {extra}"


def test_the_direct_tier_is_the_employer_side():
    for name in ("greenhouse", "lever", "ashby", "workable", "smartrecruiters",
                 "teamtailor", "recruitee", "workday", "join",
                 "euraxess", "academics", "myscience", "wissenschaftskarriere",
                 "usajobs", "governmentjobs"):
        assert sources.is_direct(name), name
        assert sources.direct_note(name), name


def test_an_aggregator_is_not_direct():
    for name in ("arbeitnow", "himalayas", "linkedin", "stepstone", "xing"):
        assert not sources.is_direct(name), name
        assert sources.direct_note(name) == "", name


def test_an_unknown_source_is_not_direct():
    """Config can name a board this build does not know; it earns no claim."""
    assert not sources.is_direct("some_future_board")
    assert sources.direct_note("some_future_board") == ""


# --- how old is it ---------------------------------------------------------

def test_age_uses_the_boards_own_date_when_it_has_one():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    row = {"posted_at": "2026-10-15", "first_seen_at": "2026-10-17T09:00:00+00:00"}
    assert api.posting_age_days(row, now=now) == pytest.approx(2.5)


def test_age_falls_back_to_when_we_first_saw_it():
    """No date from the board means the honest clock is our own first sighting.

    It can only make a posting look younger than it is, never older -- which is
    the safe direction for a window whose promise is "you are early".
    """
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    row = {"posted_at": "", "first_seen_at": "2026-10-17T06:00:00+00:00"}
    assert api.posting_age_days(row, now=now) == pytest.approx(0.25)


def test_a_future_date_reads_as_new_today_not_negative():
    """karriere_at pre-publishes: measured, mean first_seen - posted_at =
    -1.95 days, so a negative age is real data and must not read as "fresher
    than new"."""
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    assert api.posting_age_days({"posted_at": "2026-10-20"}, now=now) == 0.0


def test_an_unreadable_or_absent_date_is_unknown_not_zero():
    """Unknown must not look like "posted today" or a posting with no date would
    jump into the fresh window."""
    assert api.posting_age_days({"posted_at": "not a date"}) is None
    assert api.posting_age_days({}) is None
    assert api.posting_age_days({"posted_at": "", "first_seen_at": ""}) is None


# --- has it gone public yet ------------------------------------------------

def _conn(tmp_path):
    return dbm.connect(tmp_path / "t.db")


def _add(conn, *, source, url, posted_at="", title="Softwareentwickler",
         company="ACME GmbH", location="Berlin", seen="2026-10-01T09:00:00+00:00"):
    """Insert one posting. `seen` is when THAT board first showed it to us, which
    is the clock the lead-time report measures, so tests that care about the gap
    pass real timestamps rather than the "t1" placeholder."""
    run = dbm.start_run(conn)
    p = Posting(source=source, title=title, company=company, location=location,
                url=url, external_id=url, posted_at=posted_at)
    pid, _, dup_of = dbm.upsert(conn, p, score_posting(PREFS, p), run, seen)
    return pid, dup_of


def test_nothing_republished_yet_is_an_empty_list(tmp_path):
    conn = _conn(tmp_path)
    pid, dup = _add(conn, source="greenhouse", url="https://g/1")
    assert dup is None
    assert api.republished_as(conn, pid) == []


def test_a_later_row_from_another_board_counts_as_republished(tmp_path):
    """The same role arriving from somewhere else is the clock running out, and
    it is the row that arrives SECOND that has dup_of set."""
    conn = _conn(tmp_path)
    first, _ = _add(conn, source="greenhouse", url="https://g/1")
    second, dup_of = _add(conn, source="arbeitnow", url="https://a/1")
    assert dup_of == first, "the second row must point at the first"

    twins = api.republished_as(conn, first)
    assert [t["source"] for t in twins] == ["arbeitnow"]
    assert twins[0]["direct"] is False
    # and the republisher itself is nobody's twin
    assert api.republished_as(conn, second) == []


def test_a_board_reposting_its_own_listing_is_not_a_republisher(tmp_path):
    """arbeitnow reposts a role under a new slug, so it can be its own twin.

    Measured on the real store: of 27,357 postings only 73 roles appear on more
    than one SOURCE, while dup rows number in the hundreds -- most of them a
    board duplicating itself. Reporting that as "also on arbeitnow" while the
    user is looking at arbeitnow would be a lie about the one thing this feature
    exists to say.
    """
    conn = _conn(tmp_path)
    first, _ = _add(conn, source="arbeitnow", url="https://a/1")
    second, dup_of = _add(conn, source="arbeitnow", url="https://a/1-reslug")
    assert dup_of == first, "the re-slugged copy is still recorded as a dup"

    assert api.republished_as(conn, first, "arbeitnow") == []
    assert api.republished_map(conn, [(first, "arbeitnow")]) == {}


def test_a_real_second_board_still_shows_through_its_own_noise(tmp_path):
    """The same-source copy must not mask a genuine second board."""
    conn = _conn(tmp_path)
    first, _ = _add(conn, source="arbeitnow", url="https://a/1")
    _add(conn, source="arbeitnow", url="https://a/1-reslug")
    _add(conn, source="greenhouse", url="https://g/1")

    twins = api.republished_as(conn, first, "arbeitnow")
    assert [t["source"] for t in twins] == ["greenhouse"]
    assert twins[0]["direct"] is True


def test_the_bulk_lookup_agrees_with_the_single_lookup(tmp_path):
    conn = _conn(tmp_path)
    first, _ = _add(conn, source="greenhouse", url="https://g/1")
    _add(conn, source="arbeitnow", url="https://a/1")
    other, _ = _add(conn, source="lever", url="https://l/1", title="PHP Developer")

    bulk = api.republished_map(conn, [(first, "greenhouse"), (other, "lever")])
    assert bulk.get(first) == api.republished_as(conn, first, "greenhouse")
    assert other not in bulk, "a posting with no twins must not appear at all"


# --- what the card reads ---------------------------------------------------

def test_posting_dict_carries_the_mark(tmp_path):
    conn = _conn(tmp_path)
    pid, _ = _add(conn, source="greenhouse", url="https://g/1",
                  posted_at=(datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d"))
    row = conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()
    out = api.posting_dict(row)
    assert out["direct"] is True
    assert out["direct_note"]
    assert out["age_days"] == pytest.approx(2, abs=1)
    assert out["republished"] == []


def test_posting_dict_survives_a_row_from_another_era():
    """A hand-made row (older DB, older schema, a test) must not break the view."""
    out = api.posting_dict({"title": "x", "source": "arbeitnow", "reasons": "not json"})
    assert out["direct"] is False
    assert out["direct_note"] == ""
    assert out["age_days"] is None
    assert out["republished"] == []
    assert out["reasons"] == []


def test_list_matches_marks_the_survivor_and_hides_the_copy(tmp_path):
    """The grid shows the row we saw FIRST, with the republisher named on it."""
    conn = _conn(tmp_path)
    first, _ = _add(conn, source="greenhouse", url="https://g/1")
    _add(conn, source="arbeitnow", url="https://a/1")

    rows = api.list_matches(conn, min_score=0)
    assert [r["id"] for r in rows] == [first]
    assert rows[0]["direct"] is True
    assert [t["source"] for t in rows[0]["republished"]] == ["arbeitnow"]


# --- the two-sided contract with the window --------------------------------

def test_the_window_reads_the_fields_the_api_sends():
    """A rename on one side only is silent: the badge just stops appearing."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "jobscout" / "web"
    js = (root / "app.js").read_text(encoding="utf-8")
    html = (root / "index.html").read_text(encoding="utf-8")

    for field in ("r.direct", "r.age_days", "r.republished", "insider_days"):
        assert field in js, f"app.js no longer reads {field}"
    assert 'id="fresh"' in html, "the filter control is gone"
    assert "freshFromSource" in js, "the filter rule is gone"


def test_the_window_defaults_to_a_week_when_the_config_has_no_key(tmp_path):
    """`insider_days` is new, so an existing config file will not have it."""
    conn = _conn(tmp_path)
    out = api.stats(conn, {"min_score": 6, "strong_threshold": 10})
    assert out["insider_days"] == 7


# --- the employers you follow ----------------------------------------------

class _FakeFetcher:
    """Answers the one URL the detection needs, and records what was asked."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def get(self, url):
        self.asked.append(url)
        for frag, reply in self.answers.items():
            if frag in url:
                return reply
        raise RuntimeError(f"no answer stubbed for {url}")


def test_the_watchlist_reflects_the_config(ws):
    from jobscout.config import load_config
    cfg = load_config()
    rows = api.watchlist(cfg)
    assert rows, "the default config ships with some companies"
    by_name = {r["source"]: r for r in rows}
    assert by_name["greenhouse"]["tokens"][:1] == ["getyourguide"]
    assert by_name["greenhouse"]["count"] == 4
    assert by_name["greenhouse"]["enabled"] is True
    assert by_name["greenhouse"]["note"], "each board says why it counts"
    # biggest list first, so the ones you invested in are at the top
    counts = [r["count"] for r in rows]
    assert counts == sorted(counts, reverse=True)


def test_removing_a_company_keeps_every_comment(ws):
    """The config file is documentation; an edit must not eat it.

    This is the same rule as every other config write in this project, and the
    one a naive yaml round-trip breaks: 118 comment lines in a fresh file.
    """
    from jobscout.config import load_config
    cfg = load_config()
    before = cfg.path.read_text(encoding="utf-8")
    n_comments = sum(1 for ln in before.splitlines() if ln.strip().startswith("#"))

    out = api.watch_remove(cfg, "greenhouse", "celonis")
    assert out["ok"] and out["status"] == "removed"

    after = cfg.path.read_text(encoding="utf-8")
    assert sum(1 for ln in after.splitlines() if ln.strip().startswith("#")) == n_comments
    fresh = load_config()                       # still valid YAML
    tokens = api.watchlist(fresh)[0:10]
    gh = next(r for r in tokens if r["source"] == "greenhouse")
    assert "celonis" not in gh["tokens"]
    assert gh["count"] == 3
    assert "getyourguide" in gh["tokens"], "the others survive"


def test_removing_something_absent_changes_nothing(ws):
    """Idempotent, and honest about which case it was.

    `ok` either way -- the user asked for it to be gone and it is gone -- but the
    status says whether it was ever there, so the screen can be specific instead
    of claiming a removal that never happened.
    """
    from jobscout.config import load_config
    cfg = load_config()
    before = cfg.path.read_text(encoding="utf-8")
    out = api.watch_remove(cfg, "greenhouse", "not-a-company")
    assert out["status"] == "missing"
    assert out["ok"] is True, "the end state is the one that was asked for"
    assert cfg.path.read_text(encoding="utf-8") == before, "byte-identical"


def test_following_a_company_validates_before_writing(ws):
    """Nothing is added until the board's own API answers with jobs.

    A token that validates to an empty board looks exactly like a company with
    no vacancies, and the user would never find out which one it was.
    """
    from jobscout.config import load_config
    cfg = load_config()
    fetcher = _FakeFetcher({
        "boards-api.greenhouse.io/v1/boards/newco/jobs": (200, '{"jobs": [1, 2, 3]}'),
    })
    out = api.watch_add(cfg, "https://boards.greenhouse.io/newco", fetcher=fetcher)
    assert out["ok"] is True, out
    assert out["board"] == "greenhouse" and out["token"] == "newco"
    assert out["added"] in ("added", "exists")

    fresh = load_config()
    gh = next(r for r in api.watchlist(fresh) if r["source"] == "greenhouse")
    assert "newco" in gh["tokens"]


def test_a_board_that_answers_with_nothing_adds_nothing(ws):
    from jobscout.config import load_config
    cfg = load_config()
    before = cfg.path.read_text(encoding="utf-8")
    fetcher = _FakeFetcher({
        "boards-api.greenhouse.io/v1/boards/emptyco/jobs": (404, "not found"),
    })
    out = api.watch_add(cfg, "https://boards.greenhouse.io/emptyco", fetcher=fetcher)
    assert out["ok"] is False
    assert cfg.path.read_text(encoding="utf-8") == before, "config untouched"


def test_a_page_with_no_board_says_where_to_look(ws):
    """The hint matters: a careers page's own URL often has no board in it, and
    a dead end with no next step is the worst outcome for a non-technical user."""
    from jobscout.config import load_config
    cfg = load_config()
    fetcher = _FakeFetcher({"example.com/careers": (200, "<html>no board here</html>")})
    out = api.watch_add(cfg, "https://example.com/careers", fetcher=fetcher)
    assert out["ok"] is False
    assert "click into a role" in out["hint"]


def test_the_settings_screen_calls_the_three_company_endpoints():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "jobscout" / "web" / "app.js").read_text(encoding="utf-8")
    for call in ("pywebview.api.watchlist()", "pywebview.api.watch_add(",
                 "pywebview.api.watch_remove("):
        assert call in js, f"the pane no longer calls {call}"


# --- the head start, measured ----------------------------------------------

def test_lead_times_counts_both_directions(tmp_path):
    """The report has to be able to say "the aggregator was first", because
    sometimes it is: 8 of the 21 usable pairs on the real store."""
    conn = _conn(tmp_path)
    _add(conn, source="greenhouse", url="https://g/1", seen="2026-10-01T09:00:00+00:00")
    _add(conn, source="arbeitnow", url="https://a/1", seen="2026-10-04T09:00:00+00:00")
    _add(conn, source="arbeitnow", url="https://a/2", title="Data Engineer",
         seen="2026-10-01T09:00:00+00:00")
    _add(conn, source="lever", url="https://l/2", title="Data Engineer",
         seen="2026-10-09T09:00:00+00:00")

    out = api.lead_times(conn)
    assert out["pairs"] == 2
    assert out["comparable"] == 2, "both pairs have one side republishing the other"
    assert out["direct_first"] == 1, "greenhouse was the employer board here"
    assert out["aggregator_first"] == 1, "and here the aggregator genuinely was first"
    assert out["median_days"] == 5.5


def test_lead_times_says_nothing_when_there_is_nothing(tmp_path):
    """An empty report must not read as "zero lag" or as a win."""
    conn = _conn(tmp_path)
    _add(conn, source="greenhouse", url="https://g/1")
    out = api.lead_times(conn)
    assert out == {"pairs": 0, "comparable": 0, "direct_first": 0,
                   "aggregator_first": 0, "median_days": None}


def test_a_board_duplicating_itself_is_not_a_pair(tmp_path):
    """The same-source reposts that made "also on arbeitnow" wrong would
    otherwise pad the lead-time report with a lag of zero days."""
    conn = _conn(tmp_path)
    _add(conn, source="arbeitnow", url="https://a/1")
    _add(conn, source="arbeitnow", url="https://a/1-reslug")
    assert api.lead_times(conn)["pairs"] == 0


def test_the_grid_gets_the_lead_report_without_asking_for_it(tmp_path):
    """It is one line of the settings screen, so it rides along with stats()."""
    conn = _conn(tmp_path)
    out = api.stats(conn, {})
    assert out["lead"]["pairs"] == 0
