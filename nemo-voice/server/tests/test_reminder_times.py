"""reminder_times: turn the voice agent's spoken time args into UTC fields."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import reminder_times as rt


def test_in_minutes_is_future_utc():
    at, cron = rt.trigger_from_args({"in_minutes": 30})
    assert cron is None
    parsed = datetime.strptime(at, rt.FMT).replace(tzinfo=timezone.utc)
    delta = parsed - rt.now_utc()
    assert timedelta(minutes=29) < delta < timedelta(minutes=31)


def test_in_minutes_must_be_future():
    with pytest.raises(ValueError):
        rt.trigger_from_args({"in_minutes": 0})


def test_local_time_converts_with_offset():
    """A local one-shot is stored as local-minus-offset in UTC."""
    future_local = (rt.now_utc() + timedelta(hours=rt.OFFSET_HOURS + 2)).strftime(
        "%Y-%m-%d %H:%M"
    )
    at, cron = rt.trigger_from_args({"local_time": future_local})
    assert cron is None
    assert datetime.strptime(at, rt.FMT) < datetime.strptime(
        future_local, "%Y-%m-%d %H:%M"
    )


def test_local_time_in_past_rejected():
    past_local = (rt.now_utc() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    with pytest.raises(ValueError):
        rt.trigger_from_args({"local_time": past_local})


def test_daily_makes_cron_in_utc():
    at, cron = rt.trigger_from_args({"daily_time": "07:30"})
    # 07:30 local with offset 5 → 02:30 UTC → cron "30 2 * * *".
    expected_hour = (7 - rt.OFFSET_HOURS) % 24
    assert cron == f"30 {expected_hour} * * *"
    assert at  # first trigger resolved


def test_no_time_arg_raises():
    with pytest.raises(ValueError):
        rt.trigger_from_args({})
