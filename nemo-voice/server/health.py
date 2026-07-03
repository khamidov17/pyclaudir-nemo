"""Health rhythms — gentle nudges from Avazbek's own habit logs.

A proactive watcher over the ledger's `habit` rows. It notices patterns worth
a friendly word — short sleep streaks, a missing workout — framed like a
friend, never clinical, never medical advice. Registered in
watchers.WATCHERS; low severity, throttled to once per pattern per day.

See docs/design-vision-health.md.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import ledger
import memory_store

LOG = logging.getLogger("nemo.health")

_UTC_OFFSET_HOURS = int(os.environ.get("NEMO_UTC_OFFSET", "5"))
_SLEEP_HOURS_MIN = float(os.environ.get("HEALTH_SLEEP_MIN", "6"))
_SLEEP_STREAK = int(os.environ.get("HEALTH_SLEEP_STREAK", "3"))
_WORKOUT_GAP_DAYS = int(os.environ.get("HEALTH_WORKOUT_GAP", "5"))
_WORKOUT_NAMES = ("gym", "run", "workout", "exercise", "walk")

_META_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS health_meta (key TEXT PRIMARY KEY, value TEXT)"
)


def _now_local() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        hours=_UTC_OFFSET_HOURS
    )


def _today() -> str:
    return _now_local().strftime("%Y-%m-%d")


def _sleep_streak() -> int:
    """Consecutive most-recent nights with sleep < the healthy minimum."""
    rows = ledger.recent_habits("sleep", days=14)
    streak = 0
    for _ts, hours in rows:
        if 0 < hours < _SLEEP_HOURS_MIN:
            streak += 1
        else:
            break
    return streak


def _days_since_workout() -> int | None:
    """Days since any exercise habit, or None if one was logged today / never
    logged at all (nothing to nag about from an empty history)."""
    latest: str | None = None
    for name in _WORKOUT_NAMES:
        rows = ledger.recent_habits(name, days=_WORKOUT_GAP_DAYS + 5)
        if rows and (latest is None or rows[0][0] > latest):
            latest = rows[0][0]
    if latest is None:
        return None
    last_day = datetime.strptime(latest[:10], "%Y-%m-%d")
    return (datetime.strptime(_today(), "%Y-%m-%d") - last_day).days


def _fired_today(pattern: str) -> bool:
    con = memory_store.connect()
    con.execute(_META_SCHEMA)
    try:
        row = con.execute(
            "SELECT value FROM health_meta WHERE key=?", (pattern,)
        ).fetchone()
        return bool(row) and row[0] == _today()
    finally:
        con.close()


def poll_health() -> list:
    import watchers

    out = []
    streak = _sleep_streak()
    if streak >= _SLEEP_STREAK and not _fired_today("sleep"):
        out.append(
            watchers.Event(
                "health",
                "normal",
                f"you've slept under {int(_SLEEP_HOURS_MIN)} hours {streak} nights "
                "running — take it easy tonight?",
                "health:sleep",
                0,
            )
        )
    gap = _days_since_workout()
    if gap is not None and gap >= _WORKOUT_GAP_DAYS and not _fired_today("workout"):
        out.append(
            watchers.Event(
                "health",
                "normal",
                f"no workout logged in {gap} days — fancy a move today?",
                "health:workout",
                0,
            )
        )
    return out


def ack_health(event) -> None:
    pattern = "sleep" if event.key.endswith("sleep") else "workout"
    con = memory_store.connect()
    con.execute(_META_SCHEMA)
    try:
        con.execute(
            "INSERT OR REPLACE INTO health_meta (key, value) VALUES (?, ?)",
            (pattern, _today()),
        )
        con.commit()
    finally:
        con.close()
