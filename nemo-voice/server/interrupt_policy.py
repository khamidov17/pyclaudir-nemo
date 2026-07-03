"""When may Nemo interrupt? SPEAK into the live session, PUSH to the phone,
or DEFER until a better moment.

Fails closed: anything uncertain becomes PUSH (a notification he can ignore)
rather than SPEAK (a voice barging in). Quiet hours defer everything except
critical events.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from enum import Enum


class Decision(str, Enum):
    SPEAK = "speak"
    PUSH = "push"
    DEFER = "defer"


_SEVERITIES = ("low", "normal", "high", "critical")
_QUIET_START = int(os.environ.get("VOICE_QUIET_START", "23"))
_QUIET_END = int(os.environ.get("VOICE_QUIET_END", "8"))
_UTC_OFFSET_HOURS = int(os.environ.get("NEMO_UTC_OFFSET", "5"))


def local_hour() -> int:
    now = datetime.now(timezone.utc) + timedelta(hours=_UTC_OFFSET_HOURS)
    return now.hour


def _in_quiet_hours(hour: int) -> bool:
    if _QUIET_START <= _QUIET_END:
        return _QUIET_START <= hour < _QUIET_END
    return hour >= _QUIET_START or hour < _QUIET_END


def decide(severity: str, live_session: bool, hour: int | None = None) -> Decision:
    """Route one proactive event. Unknown severity is treated as 'normal'
    but never spoken — fail closed to PUSH."""
    h = local_hour() if hour is None else hour
    known = severity in _SEVERITIES
    if _in_quiet_hours(h):
        return Decision.PUSH if severity == "critical" else Decision.DEFER
    if severity == "low":
        # Gentle: a quiet phone push, never a spoken interruption. NOT defer —
        # DEFER + a never-changing severity would loop forever, never delivered.
        return Decision.PUSH
    if not known:
        return Decision.PUSH
    if live_session:
        return Decision.SPEAK
    return Decision.PUSH
