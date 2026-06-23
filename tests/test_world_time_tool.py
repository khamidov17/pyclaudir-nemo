"""WorldTimeTool — zone resolution and deterministic formatting.

Wall-clock nondeterminism is avoided by injecting a fixed UTC ``now`` into
the pure ``time_in_zone`` helper, so we assert exact offsets/strings.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pyclaudir.tools.base import ToolContext
from pyclaudir.tools.world_time import (
    TimezoneError,
    WorldTimeArgs,
    WorldTimeTool,
    resolve_zone,
    time_in_zone,
)


# A fixed instant well clear of any DST boundary for the zones we test.
FIXED_UTC = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return FIXED_UTC


# ---------------------------------------------------------------------------
# Zone resolution
# ---------------------------------------------------------------------------


def test_resolve_iana_name() -> None:
    assert str(resolve_zone("Asia/Tokyo")) == "Asia/Tokyo"


@pytest.mark.parametrize(
    "alias,iana",
    [
        ("tokyo", "Asia/Tokyo"),
        ("TOKYO", "Asia/Tokyo"),
        ("new york", "America/New_York"),
        ("nyc", "America/New_York"),
        ("london", "Europe/London"),
        ("utc", "UTC"),
        ("la", "America/Los_Angeles"),
    ],
)
def test_resolve_aliases(alias: str, iana: str) -> None:
    assert str(resolve_zone(alias)) == iana


def test_resolve_unknown() -> None:
    with pytest.raises(TimezoneError, match="unknown timezone"):
        resolve_zone("Atlantis/Lost_City")


def test_resolve_empty() -> None:
    with pytest.raises(TimezoneError):
        resolve_zone("   ")


# ---------------------------------------------------------------------------
# Deterministic formatting via injected clock
# ---------------------------------------------------------------------------


def test_tokyo_offset_is_plus_9() -> None:
    info = time_in_zone("tokyo", now=_clock)
    assert info["zone"] == "Asia/Tokyo"
    assert info["utc_offset"] == "+0900"
    # 12:00 UTC + 9h = 21:00 local
    assert info["datetime"] == "2026-01-15 21:00:00"


def test_utc_is_zero_offset() -> None:
    info = time_in_zone("utc", now=_clock)
    assert info["utc_offset"] == "+0000"
    assert info["datetime"] == "2026-01-15 12:00:00"


def test_la_winter_offset_is_minus_8() -> None:
    # mid-January = PST (no DST) → -08:00
    info = time_in_zone("Los Angeles", now=_clock)
    assert info["utc_offset"] == "-0800"
    assert info["datetime"] == "2026-01-15 04:00:00"


def test_naive_clock_assumed_utc() -> None:
    naive = datetime(2026, 1, 15, 12, 0, 0)  # no tzinfo
    info = time_in_zone("utc", now=lambda: naive)
    assert info["datetime"] == "2026-01-15 12:00:00"


# ---------------------------------------------------------------------------
# Tool wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_run_success() -> None:
    tool = WorldTimeTool(ToolContext())
    result = await tool.run(WorldTimeArgs(timezone="Asia/Tokyo"))
    assert not result.is_error
    assert result.data["zone"] == "Asia/Tokyo"
    assert "Asia/Tokyo" in result.content


@pytest.mark.asyncio
async def test_tool_run_unknown_zone() -> None:
    tool = WorldTimeTool(ToolContext())
    result = await tool.run(WorldTimeArgs(timezone="Nowhere/Nope"))
    assert result.is_error
    assert "error:" in result.content
