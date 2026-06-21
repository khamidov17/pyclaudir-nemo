"""Resolve voice-tool time args into UTC trigger strings + cron expressions.

Split out of reminders.py to keep that file under the 300-line cap. Pure time
math — no I/O, no DB. The voice agent says "in 30 minutes" / "tonight at 11pm"
/ "every day at 7am"; these helpers turn that into the UTC fields the engine's
reminder loop expects.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

OFFSET_HOURS = int(os.environ.get("NEMO_UTC_OFFSET", "5"))
FMT = "%Y-%m-%d %H:%M:%S"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def trigger_from_args(args: dict) -> tuple[str, str | None]:
    """Resolve (trigger_at_utc, cron_expr) from the voice tool args.

    Raises ValueError with a spoken-friendly message on bad/missing input.
    """
    if args.get("in_minutes") is not None:
        mins = int(args["in_minutes"])
        if mins <= 0:
            raise ValueError("the time has to be in the future")
        return (now_utc() + timedelta(minutes=mins)).strftime(FMT), None
    if args.get("daily_time"):
        return _daily(args["daily_time"])
    if args.get("local_time"):
        return _one_shot_local(args["local_time"]), None
    raise ValueError("tell me when — in how many minutes, a time, or every day")


def _one_shot_local(local_str: str) -> str:
    """Local 'YYYY-MM-DD HH:MM' → future UTC string."""
    local = datetime.strptime(local_str.strip(), "%Y-%m-%d %H:%M")
    utc = local - timedelta(hours=OFFSET_HOURS)
    utc = utc.replace(tzinfo=timezone.utc)
    if utc <= now_utc():
        raise ValueError("that time has already passed")
    return utc.strftime(FMT)


def _daily(hhmm: str) -> tuple[str, str]:
    """Local 'HH:MM' → (first-trigger UTC, daily cron in UTC)."""
    hh, mm = (int(x) for x in hhmm.strip().split(":"))
    utc_hour = (hh - OFFSET_HOURS) % 24
    cron = f"{mm} {utc_hour} * * *"
    first = _next_cron(cron)
    return first, cron


def _next_cron(cron: str) -> str:
    """First future UTC trigger for a cron expr (best-effort without croniter)."""
    try:
        from croniter import croniter

        return croniter(cron, now_utc()).get_next(datetime).strftime(FMT)
    except ImportError:  # pragma: no cover
        return (now_utc() + timedelta(minutes=1)).strftime(FMT)
