"""The "new" badge: a window in hours, not a scan counter."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jobscout.api import posting_dict


def _row(seen_hours_ago: float) -> dict:
    when = (datetime.now(timezone.utc) - timedelta(hours=seen_hours_ago)).isoformat(
        timespec="seconds")
    return {"id": 1, "first_seen_at": when, "reasons": "[]", "description": "",
            "title": "Developer", "company": "A", "location": "Berlin",
            "url": "https://x/1", "score": 7, "salary_raw": ""}


def test_an_advert_from_this_morning_is_new():
    assert posting_dict(_row(2))["is_new"] is True


def test_an_advert_from_last_week_is_not():
    assert posting_dict(_row(24 * 7))["is_new"] is False


def test_the_window_is_the_setting():
    assert posting_dict(_row(30), new_hours=48)["is_new"] is True
    assert posting_dict(_row(30), new_hours=12)["is_new"] is False


def test_zero_turns_the_badge_off():
    assert posting_dict(_row(1), new_hours=0)["is_new"] is False


def test_a_missing_date_is_not_new():
    assert posting_dict({"id": 2, "first_seen_at": "", "reasons": "[]"})["is_new"] is False
