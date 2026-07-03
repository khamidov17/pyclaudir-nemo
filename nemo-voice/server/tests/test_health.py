"""health — sleep-streak + workout-gap detection, throttle, empty history."""

from __future__ import annotations

from datetime import timedelta

import pytest

import health
import ledger


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))


def _log_sleep(hours: float, days_ago: int) -> None:
    ts = (health._now_local() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M")
    con = ledger._connect()
    con.execute(
        "INSERT INTO ledger (ts, kind, amount, currency, category, note)"
        " VALUES (?, 'habit', ?, '', 'sleep', '')",
        (ts, hours),
    )
    con.commit()
    con.close()


def _log_workout(name: str, days_ago: int) -> None:
    ts = (health._now_local() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M")
    con = ledger._connect()
    con.execute(
        "INSERT INTO ledger (ts, kind, amount, currency, category, note)"
        " VALUES (?, 'habit', 0, '', ?, '')",
        (ts, name),
    )
    con.commit()
    con.close()


def test_no_history_no_nudge():
    assert health.poll_health() == []


def test_sleep_streak_fires():
    for d in range(4):
        _log_sleep(5.0, d)
    events = health.poll_health()
    assert any(e.key == "health:sleep" for e in events)
    ev = next(e for e in events if e.key == "health:sleep")
    assert "4 nights" in ev.text and ev.severity == "normal"


def test_good_sleep_breaks_streak():
    _log_sleep(5.0, 0)
    _log_sleep(8.0, 1)  # good night breaks it
    _log_sleep(5.0, 2)
    assert not any(e.key == "health:sleep" for e in health.poll_health())


def test_workout_gap_fires_and_recent_does_not():
    _log_workout("gym", 7)
    assert any(e.key == "health:workout" for e in health.poll_health())
    _log_workout("run", 1)  # recent workout → no nag
    assert not any(e.key == "health:workout" for e in health.poll_health())


def test_throttled_once_per_day():
    for d in range(4):
        _log_sleep(5.0, d)
    events = health.poll_health()
    sleep_ev = next(e for e in events if e.key == "health:sleep")
    health.ack_health(sleep_ev)
    assert not any(e.key == "health:sleep" for e in health.poll_health())


def test_registered_in_watchers():
    import watchers

    assert any(name == "health" for name, _p, _a in watchers.WATCHERS)
