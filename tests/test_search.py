"""Looking inside the advert prose.

This is the part of the store nothing else could read: the titles and companies
were searchable from the window, and the text underneath them -- tens of
megabytes of it, most of what is stored -- was write-only. The risks are
specific and each one is a test here: a MATCH expression is its own language so
user text must not reach it raw, an external-content index cannot tell you it
was never built, and an index that quietly stops following writes is worse than
no index, because it looks like it works.
"""
import pytest

from jobscout import api, db as dbm

ADVERT = ("Wir suchen eine Entwicklerin mit Erfahrung in Laravel und Vue. "
          "Kenntnisse in der Denkmalpflege sind von Vorteil, ebenso "
          "Quastenflossenkenntnisse.")


def _conn(tmp_path):
    return dbm.connect(tmp_path / "t.db")


def _post(conn, *, description, title="Softwareentwickler", source="greenhouse",
          url="https://x/1"):
    run = dbm.start_run(conn)
    conn.execute(
        "INSERT INTO postings (fingerprint, source, company, title, location, url,"
        " first_seen_at, first_seen_run, last_seen_at, last_seen_run, description)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (f"fp-{url}", source, "ACME", title, "Berlin", url,
         "2026-10-01T09:00:00+00:00", run, "2026-10-01T09:00:00+00:00", run, description),
    )
    conn.commit()
    return conn.execute("SELECT id FROM postings WHERE fingerprint=?", (f"fp-{url}",)).fetchone()[0]


# --- the query builder -----------------------------------------------------

@pytest.mark.parametrize("typed", [
    "", "   ", "((( ", 'php" AND', "NEAR(", "OR", "a*", "NOT laravel", '"', "\\",
])
def test_what_a_person_types_cannot_be_a_syntax_error(tmp_path, typed):
    """MATCH has its own language, and a stray bracket in a search box must not
    become an exception from inside sqlite."""
    conn = _conn(tmp_path)
    _post(conn, description=ADVERT)

    out = dbm._fts_query(typed)
    assert isinstance(out, str)
    assert "(" not in out and ")" not in out
    dbm.search_text(conn, typed)          # the real proof: it runs


@pytest.mark.parametrize("typed,expected", [
    ("laravel", '"laravel"*'),
    ("laravel vue", '"laravel"* AND "vue"*'),
    ("Laravel, Vue!", '"Laravel"* AND "Vue"*'),
    ("a laravel", '"laravel"*'),          # single letters are noise, not words
])
def test_words_become_prefix_terms(typed, expected):
    assert dbm._fts_query(typed) == expected


# --- finding things --------------------------------------------------------

def test_it_finds_words_that_are_only_in_the_advert(tmp_path):
    conn = _conn(tmp_path)
    _post(conn, description=ADVERT, title="Unrelated title")
    hits = dbm.search_text(conn, "Denkmalpflege")
    assert len(hits) == 1
    assert "Denkmalpflege" in hits[0]["around"], "the matched words are shown"


def test_a_partial_word_matches(tmp_path):
    """People type half a word, and a search that needs the whole one is a
    search that says "nothing found" about something that is right there."""
    conn = _conn(tmp_path)
    _post(conn, description=ADVERT)
    assert len(dbm.search_text(conn, "Quasten")) == 1
    assert len(dbm.search_text(conn, "denkmalpfleg")) == 1


def test_all_the_words_have_to_be_there(tmp_path):
    conn = _conn(tmp_path)
    _post(conn, description=ADVERT)
    assert len(dbm.search_text(conn, "laravel vue")) == 1
    assert dbm.search_text(conn, "laravel kubernetes") == []


def test_umlauts_match_without_their_dots(tmp_path):
    """Someone typing on a phone or in a hurry writes "grabung", and the advert
    says "Grabung" or "Gräbung". remove_diacritics 2 is what makes both land."""
    conn = _conn(tmp_path)
    _post(conn, description="Eine Grabung im Süden.", url="https://x/a")
    _post(conn, description="Eine Gräbung im Norden.", url="https://x/b")
    assert len(dbm.search_text(conn, "grabung")) == 2
    assert len(dbm.search_text(conn, "gräbung")) == 2


def test_partial_words_count_from_their_start(tmp_path):
    """The rule, stated because it is not obvious and I got it wrong first:

        "ausgrab" finds Ausgrabung, "grab" finds Grabung, and neither finds the
        other's word.

    A German compound is ONE word to the tokenizer, so "grabung" does not reach
    inside "Ausgrabung". Substring search would need the trigram tokenizer,
    which multiplies the index for a search box nobody asked to be fuzzy; the
    honest fix is to say how it works and suggest typing the start of the word.
    """
    conn = _conn(tmp_path)
    _post(conn, description="Eine Ausgrabung im Süden.", url="https://x/a")
    _post(conn, description="Eine Grabung im Norden.", url="https://x/b")
    assert len(dbm.search_text(conn, "ausgrab")) == 1, "start of Ausgrabung"
    assert len(dbm.search_text(conn, "grab")) == 1, "start of Grabung, not of Ausgrabung"
    assert len(dbm.search_text(conn, "grabung")) == 1
    assert len(dbm.search_text(conn, "ausgrabung")) == 1


def test_nothing_searchable_asks_nothing(tmp_path):
    conn = _conn(tmp_path)
    _post(conn, description=ADVERT)
    for empty in ("", "   ", "((( ", "\u2014"):
        assert dbm.search_text(conn, empty) == []


def test_the_api_says_when_it_stopped_at_the_cap(tmp_path):
    """Otherwise "the first 50" reads as "there are 50"."""
    conn = _conn(tmp_path)
    for i in range(5):
        _post(conn, description=ADVERT, url=f"https://x/{i}")
    got = api.search_text(conn, "laravel", limit=3)
    assert got["count"] == 3
    assert got["limited"] is True
    assert got["limit"] == 3
    wide = api.search_text(conn, "laravel", limit=50)
    assert wide["count"] == 5 and wide["limited"] is False


# --- keeping the index honest ----------------------------------------------

def test_the_index_is_built_once_and_recorded(tmp_path):
    conn = _conn(tmp_path)
    _post(conn, description=ADVERT)
    assert dbm.ensure_fts(conn) is True, "first call builds it"
    assert dbm.ensure_fts(conn) is False, "and never again"
    row = conn.execute("SELECT value FROM meta WHERE key=?", (dbm.FTS_STAMP,)).fetchone()
    assert row and row["value"], "the stamp says when it was built"


def test_searching_builds_the_index_by_itself(tmp_path):
    """An external-content index cannot tell you it is empty: a query over it
    reads the CONTENT table, so a store that was never indexed looks like it
    searched fine and found nothing."""
    conn = _conn(tmp_path)
    _post(conn, description=ADVERT)
    assert dbm.search_text(conn, "denkmalpflege"), "found without an explicit build"


def test_the_count_describes_what_the_window_can_show(tmp_path):
    """Hidden rows would make "82 postings match" sit next to two cards, which
    reads as a broken search rather than a filtered one."""
    conn = _conn(tmp_path)
    shown = _post(conn, description=ADVERT, url="https://x/vis")
    hidden = _post(conn, description=ADVERT, url="https://x/hid")
    conn.execute("UPDATE postings SET filtered=1, filtered_reason='title_deny'"
                 " WHERE id=?", (hidden,))
    conn.commit()

    assert [h["id"] for h in dbm.search_text(conn, "laravel")] == [shown]
    both = dbm.search_text(conn, "laravel", include_hidden=True)
    assert {h["id"] for h in both} == {shown, hidden}, "the CLI can still ask for them"


def test_the_index_follows_every_write(tmp_path):
    conn = _conn(tmp_path)
    pid = _post(conn, description="Einzigartigeswort hierselbst")
    assert len(dbm.search_text(conn, "Einzigartigeswort")) == 1

    conn.execute("UPDATE postings SET description=? WHERE id=?",
                 ("voellig ersetzter Text", pid))
    conn.commit()
    assert dbm.search_text(conn, "Einzigartigeswort") == []
    assert len(dbm.search_text(conn, "ersetzter")) == 1

    conn.execute("DELETE FROM postings WHERE id=?", (pid,))
    conn.commit()
    assert dbm.search_text(conn, "ersetzter") == []


def test_the_window_and_the_cli_use_the_same_search():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    js = (root / "jobscout" / "web" / "app.js").read_text(encoding="utf-8")
    cli = (root / "jobscout" / "cli.py").read_text(encoding="utf-8")
    assert "pywebview.api.search_text(" in js
    assert "dbm.search_text(" in cli, "the CLI must not grow its own search"
    assert "around" in js, "the matching words have to reach the card"
