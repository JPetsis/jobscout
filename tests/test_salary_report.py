"""What the postings advertise, and the caveats that make the number honest.

A pay median is the easiest thing in this app to mislead with: 16% of the store
carries a figure, and one board supplies most of them. So the tests here are as
much about what the report REFUSES to do -- mix currencies, dress up a single
advert as a distribution, count a hidden posting -- as about the arithmetic.
"""
import pytest
from datetime import datetime, timedelta, timezone

from jobscout import api, db as dbm, salary as salmod


def _conn(tmp_path):
    return dbm.connect(tmp_path / "t.db")


def _post(conn, *, title="Softwareentwickler", city="Berlin", source="germantechjobs",
          url="https://x/1", top=None, low=None, period="year", currency="EUR",
          posted_days_ago=1, filtered=0):
    run = dbm.start_run(conn)
    posted = (datetime.now(timezone.utc) - timedelta(days=posted_days_ago)).date().isoformat()
    conn.execute(
        "INSERT INTO postings (fingerprint, source, company, title, location, url,"
        " first_seen_at, first_seen_run, last_seen_at, last_seen_run, posted_at,"
        " salary_min, salary_max, salary_currency, salary_period, salary_annual_max,"
        " filtered, filtered_reason)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"fp-{url}", source, "ACME", title, city, url,
         "2026-10-01T09:00:00+00:00", run, "2026-10-01T09:00:00+00:00", run, posted,
         low, top, currency, period,
         (top * salmod._ANNUAL.get(period, 1.0)) if top else None,
         filtered, "title_deny" if filtered else ""),
    )
    conn.commit()
    return conn.execute("SELECT id FROM postings WHERE fingerprint=?", (f"fp-{url}",)).fetchone()[0]


# --- the arithmetic --------------------------------------------------------

def test_the_band_is_the_middle_half_and_the_median(tmp_path):
    conn = _conn(tmp_path)
    for i, top in enumerate([50_000, 60_000, 70_000, 80_000, 90_000]):
        _post(conn, url=f"https://x/{i}", top=top)
    rep = api.salary_report(conn, words=["entwickler"])
    band = rep["max_annual"]
    assert band["median"] == 70_000
    assert band["p25"] < band["median"] < band["p75"]
    assert band["min"] == 50_000 and band["max"] == 90_000


def test_one_number_is_not_a_distribution_but_it_is_still_reported(tmp_path):
    """The window and the CLI say "too few to describe a range"; the report
    itself must not invent one, so the numbers are the same number."""
    conn = _conn(tmp_path)
    _post(conn, top=70_000)
    rep = api.salary_report(conn, words=["entwickler"])
    assert rep["with_salary"] == 1
    assert rep["max_annual"] == {"p25": 70000, "median": 70000, "p75": 70000,
                                 "min": 70000, "max": 70000}


def test_the_minimum_side_is_annualised_with_the_same_multiplier(tmp_path):
    conn = _conn(tmp_path)
    _post(conn, low=4_000, top=5_000, period="month")     # 48k - 60k per year
    rep = api.salary_report(conn, words=["entwickler"])
    assert rep["max_annual"]["median"] == 60_000
    assert rep["min_annual"]["median"] == 48_000


def test_currencies_are_never_mixed(tmp_path):
    """EUR and CHF in one distribution is not a number, it is a mistake with a
    comma in it."""
    conn = _conn(tmp_path)
    for i in range(3):
        _post(conn, url=f"https://x/e{i}", top=60_000, currency="EUR")
    _post(conn, url="https://x/ch", top=110_000, currency="CHF")

    rep = api.salary_report(conn, words=["entwickler"])
    assert rep["currency"] == "EUR"
    assert rep["max_annual"]["median"] == 60_000
    assert rep["currencies"] == {"EUR": 3, "CHF": 1}


# --- what is counted -------------------------------------------------------

def test_hidden_postings_are_not_counted(tmp_path):
    """A filtered or duplicated posting is not something the window shows, and
    the count has to match what the window can show."""
    conn = _conn(tmp_path)
    _post(conn, url="https://x/vis", top=60_000)
    _post(conn, url="https://x/hid", top=200_000, filtered=1)
    rep = api.salary_report(conn, words=["entwickler"])
    assert rep["postings"] == 1
    assert rep["with_salary"] == 1
    assert rep["max_annual"]["max"] == 60_000


def test_coverage_is_part_of_the_answer(tmp_path):
    """The number that says how much to trust the number."""
    conn = _conn(tmp_path)
    _post(conn, url="https://x/a", top=60_000)
    for i in range(3):
        _post(conn, url=f"https://x/n{i}", top=None)
    rep = api.salary_report(conn, words=["entwickler"])
    assert rep["postings"] == 4
    assert rep["with_salary"] == 1


def test_the_words_filter_the_title_and_the_city_filters_the_place(tmp_path):
    conn = _conn(tmp_path)
    _post(conn, url="https://x/a", title="Senior PHP Developer", top=70_000)
    _post(conn, url="https://x/b", title="Data Engineer", top=80_000, city="München")

    php = api.salary_report(conn, words=["php"])
    assert php["with_salary"] == 1 and php["max_annual"]["median"] == 70_000

    munich = api.salary_report(conn, words=["engineer"], city="münchen")
    assert munich["with_salary"] == 1 and munich["max_annual"]["median"] == 80_000

    # the words and the city are both required, and neither alone is enough
    mismatch = api.salary_report(conn, words=["engineer"], city="berlin")
    assert mismatch["postings"] == 0, "the engineer role is in München, not Berlin"


def test_a_date_window_only_counts_postings_whose_date_is_known(tmp_path):
    """A store two weeks old cannot say anything about a year, and including
    undated postings would hide that rather than show it."""
    conn = _conn(tmp_path)
    _post(conn, url="https://x/new", top=70_000, posted_days_ago=2)
    _post(conn, url="https://x/old", top=50_000, posted_days_ago=400)
    conn.execute("UPDATE postings SET posted_at='' WHERE url='https://x/old'")
    conn.commit()

    recent = api.salary_report(conn, words=["entwickler"], days=30)
    assert recent["with_salary"] == 1
    assert recent["max_annual"]["median"] == 70_000


def test_the_report_says_which_board_supplied_the_numbers(tmp_path):
    """The skew IS the caveat: 912 of 1,322 on the real store come from one
    board, so the median is what that board advertises."""
    conn = _conn(tmp_path)
    for i in range(4):
        _post(conn, url=f"https://x/g{i}", top=60_000, source="germantechjobs")
    _post(conn, url="https://x/h", top=120_000, source="himalayas")

    rep = api.salary_report(conn, words=["entwickler"])
    assert rep["by_source"][0]["source"] == "germantechjobs"
    assert rep["by_source"][0]["count"] == 4
    assert rep["by_source"][0]["median"] == 60_000


# --- the small helpers -----------------------------------------------------

def test_annual_min_refuses_an_unknown_period(tmp_path):
    """A monthly figure read as annual is a 12x error in a pay report, so an
    unknown period is no number at all."""
    row = {"salary_min": 4000, "salary_period": ""}
    assert salmod.annual_min(row) is None
    assert salmod.annual_min({"salary_min": 4000, "salary_period": "month"}) == 48_000
    assert salmod.annual_min({"salary_min": 20, "salary_period": "hour"}) == 41_600


def test_the_window_and_the_cli_both_ask_the_same_report():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    js = (root / "jobscout" / "web" / "app.js").read_text(encoding="utf-8")
    cli = (root / "jobscout" / "cli.py").read_text(encoding="utf-8")
    assert "pywebview.api.salary_report(" in js
    assert "api.salary_report(" in cli, "the CLI must not grow its own arithmetic"
    assert "with_salary < 5" in js, "a single advert must not be shown as a range"
