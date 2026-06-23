"""ConvertUnitsTool — factor-table and temperature conversions."""

from __future__ import annotations

import pytest

from pyclaudir.tools.base import ToolContext
from pyclaudir.tools.convert_units import (
    ConversionError,
    ConvertUnitsArgs,
    ConvertUnitsTool,
    convert,
)


# ---------------------------------------------------------------------------
# Happy paths (pure function)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,frm,to,expected",
    [
        (1, "km", "m", 1000.0),
        (1000, "m", "km", 1.0),
        (1, "mi", "km", pytest.approx(1.609344)),
        (12, "in", "cm", pytest.approx(30.48)),
        (1, "kg", "g", 1000.0),
        (1, "lb", "kg", pytest.approx(0.45359237)),
        (1, "l", "ml", 1000.0),
        (1, "gal", "l", pytest.approx(3.785411784)),
        (100, "kph", "mph", pytest.approx(62.1371192, rel=1e-6)),
        (1, "gb", "mb", 1000.0),
        (1, "kib", "b", 1024.0),
        (1, "byte", "bit", 8.0),
    ],
)
def test_convert_happy(value, frm, to, expected) -> None:
    assert convert(value, frm, to) == expected


def test_identity_conversion() -> None:
    assert convert(42, "m", "m") == 42


def test_case_and_whitespace_insensitive() -> None:
    assert convert(1, " KM ", "M") == 1000.0


# ---------------------------------------------------------------------------
# Temperature (affine, handled specially)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,frm,to,expected",
    [
        (0, "c", "f", 32.0),
        (100, "c", "f", 212.0),
        (32, "f", "c", 0.0),
        (0, "c", "k", 273.15),
        (273.15, "k", "c", 0.0),
        (212, "fahrenheit", "celsius", pytest.approx(100.0)),
        (-40, "c", "f", -40.0),  # the famous crossover
        (300, "kelvin", "kelvin", 300.0),
    ],
)
def test_temperature(value, frm, to, expected) -> None:
    assert convert(value, frm, to) == expected


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_unknown_unit() -> None:
    with pytest.raises(ConversionError, match="unknown unit"):
        convert(1, "smoots", "m")


def test_cross_category_rejected() -> None:
    with pytest.raises(ConversionError, match="cannot convert"):
        convert(1, "m", "kg")


def test_temperature_to_non_temperature_rejected() -> None:
    with pytest.raises(ConversionError, match="temperature"):
        convert(1, "c", "m")


# ---------------------------------------------------------------------------
# Tool wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_run_success() -> None:
    tool = ConvertUnitsTool(ToolContext())
    result = await tool.run(ConvertUnitsArgs(value=1, from_unit="km", to_unit="m"))
    assert not result.is_error
    assert result.data["result"] == 1000.0
    assert "1000" in result.content


@pytest.mark.asyncio
async def test_tool_run_error() -> None:
    tool = ConvertUnitsTool(ToolContext())
    result = await tool.run(ConvertUnitsArgs(value=1, from_unit="m", to_unit="kg"))
    assert result.is_error
    assert "error:" in result.content
