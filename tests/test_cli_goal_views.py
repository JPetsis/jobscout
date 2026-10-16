"""Composing goals at VIEW time: `--goal` is repeatable and every given goal
must match. Definitions stay single-dimensional (goals.py's doctrine), so the
per-goal counts in `jobscout goals` keep meaning what they say."""
import types

from jobscout import cli
from jobscout import db as dbm
from jobscout.goals import Goal

PREFS = {"min_score": 6, "strong_threshold": 10, "title_boost": 3, "title_cap": 6,
         "title_terms": ["entwickler", "software"], "keywords": ["python"]}


def _lab(tmp_path):
    cfg = types.SimpleNamespace(
        root=tmp_path, active_goal="", prefs=dict(PREFS),
        goals={"permanent": Goal(name="permanent", kind="terms",
                                 terms=["unbefristet", "permanent"], scope="all"),
               "austria": Goal(name="austria", kind="location",
                               locations=["austria", "wien", "österreich"])})
    return cfg, dbm.connect(tmp_path / "jobscout.db")


def _add(conn, title, location, desc, score=13, reasons='[]'):
    n = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] + 1
    conn.execute(
        "INSERT INTO postings (fingerprint, source, title, company, location,"
        " url, description, first_seen_at, first_seen_run, last_seen_at,"
        " last_seen_run, score, reasons, filtered, status)"
        " VALUES (?, 'lab', ?, 'Acme', ?, 'https://x', ?, '2026-10-16T00:00:00', 1,"
        " '2026-10-16T00:00:00', 1, ?, ?, 0, 'new')",
        (f"fp{n}", title, location, desc, score, reasons))
    conn.commit()


def _args(**kw):
    base = dict(min_score=None, status=None, source=None, limit=100, json=False,
                all_goals=False, goal=None, filtered=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_repeatable_goal_intersects(capsys, monkeypatch, tmp_path):
    cfg, conn = _lab(tmp_path)
    _add(conn, "Python Entwickler", "Wien, Österreich", "unbefristete Stelle")
    _add(conn, "Python Entwickler", "Berlin", "unbefristete Stelle")
    _add(conn, "Python Entwickler", "Wien, Österreich", "befristet für 2 Jahre")
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    rc = cli.cmd_list(_args(goal=["permanent", "austria"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("Python Entwickler") == 1          # permanent AND austria
    assert "1 shown for goal 'permanent+austria'" in out


def test_single_goal_and_unknown_name(capsys, monkeypatch, tmp_path):
    cfg, conn = _lab(tmp_path)
    _add(conn, "Python Entwickler", "Berlin", "unbefristete Stelle",
         reasons='[["perm:unbefristet", 4]]')
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    assert cli.cmd_list(_args(goal=["permanent"])) == 0
    out = capsys.readouterr().out
    assert "1 shown for goal 'permanent'" in out
    assert "[permanent]" in out                          # canonical pill
    # an unknown name is reported, never fatal, and the rest still matches
    assert cli.cmd_list(_args(goal=["permanent", "nope"])) == 0
    out = capsys.readouterr().out
    assert "no goal named 'nope'" in out
    assert "Python Entwickler" in out


def test_combined_empty_state_names_both_goals(capsys, monkeypatch, tmp_path):
    cfg, conn = _lab(tmp_path)
    _add(conn, "Python Entwickler", "Berlin", "unbefristete Stelle")
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    rc = cli.cmd_list(_args(goal=["permanent", "austria"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing matches goals 'permanent+austria'" in out
