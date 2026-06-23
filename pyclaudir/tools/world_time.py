"""``world_time`` — current time in a named timezone or city (offline).

Uses the stdlib :mod:`zoneinfo` database — no network. Accepts IANA names
(``Asia/Tokyo``) and a small alias map for common cities (``tokyo`` →
``Asia/Tokyo``).

The wall-clock read goes through an injectable ``now`` callable so the
formatting / zone logic can be tested deterministically without depending on
the real time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from .base import BaseTool, ToolResult


class TimezoneError(ValueError):
    """Raised when a timezone / city name can't be resolved."""


# Common city / informal aliases → IANA names. IANA names themselves are
# resolved directly by ZoneInfo, so they don't need to appear here.
_CITY_ALIASES: dict[str, str] = {
    "utc": "UTC",
    "gmt": "UTC",
    "tokyo": "Asia/Tokyo",
    "japan": "Asia/Tokyo",
    "london": "Europe/London",
    "uk": "Europe/London",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "madrid": "Europe/Madrid",
    "rome": "Europe/Rome",
    "moscow": "Europe/Moscow",
    "dubai": "Asia/Dubai",
    "delhi": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "kolkata": "Asia/Kolkata",
    "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "china": "Asia/Shanghai",
    "singapore": "Asia/Singapore",
    "hongkong": "Asia/Hong_Kong",
    "hong_kong": "Asia/Hong_Kong",
    "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "auckland": "Pacific/Auckland",
    "new_york": "America/New_York",
    "newyork": "America/New_York",
    "nyc": "America/New_York",
    "chicago": "America/Chicago",
    "denver": "America/Denver",
    "los_angeles": "America/Los_Angeles",
    "losangeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "san_francisco": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "toronto": "America/Toronto",
    "sao_paulo": "America/Sao_Paulo",
    "mexico_city": "America/Mexico_City",
}


def resolve_zone(name: str) -> ZoneInfo:
    """Resolve a city alias or IANA name to a :class:`ZoneInfo`.

    Raises :class:`TimezoneError` if neither lookup succeeds.
    """
    if not isinstance(name, str) or not name.strip():
        raise TimezoneError("empty timezone name")
    raw = name.strip()
    key = raw.lower().replace(" ", "_")
    iana = _CITY_ALIASES.get(key, raw)
    try:
        return ZoneInfo(iana)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise TimezoneError(
            f"unknown timezone or city {name!r}; use an IANA name like "
            "'Asia/Tokyo' or a known city alias"
        ) from exc


def time_in_zone(
    name: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, str]:
    """Return the current time in zone ``name`` as a dict of strings.

    ``now`` is an injectable clock returning a timezone-aware UTC datetime.
    Defaults to ``datetime.now(timezone.utc)``. Naive datetimes from a
    custom clock are assumed to be UTC.
    """
    zone = resolve_zone(name)
    instant = (now or (lambda: datetime.now(timezone.utc)))()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local = instant.astimezone(zone)
    return {
        "zone": str(zone),
        "iso": local.isoformat(timespec="seconds"),
        "datetime": local.strftime("%Y-%m-%d %H:%M:%S"),
        "utc_offset": local.strftime("%z"),
        "abbreviation": local.tzname() or "",
    }


class WorldTimeArgs(BaseModel):
    timezone: str = Field(
        description=(
            "An IANA timezone name (e.g. 'Asia/Tokyo', 'Europe/London') or a "
            "common city alias (e.g. 'tokyo', 'new york', 'utc')."
        )
    )


class WorldTimeTool(BaseTool):
    name = "world_time"
    description = (
        "Get the current local time in a named timezone or city, using the "
        "offline IANA timezone database (no network). Accepts IANA names and "
        "common city aliases."
    )
    args_model = WorldTimeArgs

    async def run(self, args: WorldTimeArgs) -> ToolResult:
        try:
            info = time_in_zone(args.timezone)
        except TimezoneError as exc:
            return ToolResult(content=f"error: {exc}", is_error=True)
        return ToolResult(
            content=(
                f"{info['datetime']} ({info['abbreviation']} "
                f"UTC{info['utc_offset']}) in {info['zone']}"
            ),
            data=info,
        )
