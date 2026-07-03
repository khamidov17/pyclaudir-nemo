"""``convert_units`` — deterministic, offline unit conversion.

Supports length, mass, volume, speed, and data via factor tables (each unit
expressed relative to a canonical base unit), plus temperature (C/F/K) which
is handled with explicit formulas since it isn't a pure scaling.

All conversions are pure arithmetic on static tables — no network, fully
deterministic, trivially testable.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import BaseTool, ToolResult


class ConversionError(ValueError):
    """Raised for unknown units or cross-category conversions."""


# Each category maps a unit (and its aliases) to a factor: how many BASE units
# one of that unit equals. Base units: length=metre, mass=gram,
# volume=litre, speed=metre/second, data=byte.
_FACTOR_TABLES: dict[str, dict[str, float]] = {
    "length": {
        "mm": 0.001,
        "millimeter": 0.001,
        "millimetre": 0.001,
        "cm": 0.01,
        "centimeter": 0.01,
        "centimetre": 0.01,
        "m": 1.0,
        "meter": 1.0,
        "metre": 1.0,
        "km": 1000.0,
        "kilometer": 1000.0,
        "kilometre": 1000.0,
        "in": 0.0254,
        "inch": 0.0254,
        "inches": 0.0254,
        "ft": 0.3048,
        "foot": 0.3048,
        "feet": 0.3048,
        "yd": 0.9144,
        "yard": 0.9144,
        "mi": 1609.344,
        "mile": 1609.344,
        "miles": 1609.344,
        "nmi": 1852.0,
    },
    "mass": {
        "mg": 0.001,
        "milligram": 0.001,
        "g": 1.0,
        "gram": 1.0,
        "grams": 1.0,
        "kg": 1000.0,
        "kilogram": 1000.0,
        "kilograms": 1000.0,
        "t": 1_000_000.0,
        "tonne": 1_000_000.0,
        "oz": 28.349523125,
        "ounce": 28.349523125,
        "lb": 453.59237,
        "lbs": 453.59237,
        "pound": 453.59237,
        "pounds": 453.59237,
        "st": 6350.29318,
        "stone": 6350.29318,
    },
    "volume": {
        "ml": 0.001,
        "milliliter": 0.001,
        "millilitre": 0.001,
        "l": 1.0,
        "liter": 1.0,
        "litre": 1.0,
        "liters": 1.0,
        "litres": 1.0,
        "tsp": 0.00492892159375,
        "teaspoon": 0.00492892159375,
        "tbsp": 0.01478676478125,
        "tablespoon": 0.01478676478125,
        "floz": 0.0295735295625,
        "cup": 0.2365882365,
        "cups": 0.2365882365,
        "pt": 0.473176473,
        "pint": 0.473176473,
        "qt": 0.946352946,
        "quart": 0.946352946,
        "gal": 3.785411784,
        "gallon": 3.785411784,
        "gallons": 3.785411784,
    },
    "speed": {
        "mps": 1.0,
        "m/s": 1.0,
        "kph": 0.277777777778,
        "km/h": 0.277777777778,
        "kmh": 0.277777777778,
        "mph": 0.44704,
        "mi/h": 0.44704,
        "fps": 0.3048,
        "ft/s": 0.3048,
        "knot": 0.514444444444,
        "knots": 0.514444444444,
        "kn": 0.514444444444,
    },
    "data": {
        "b": 1.0,
        "byte": 1.0,
        "bytes": 1.0,
        "kb": 1000.0,
        "kilobyte": 1000.0,
        "mb": 1_000_000.0,
        "megabyte": 1_000_000.0,
        "gb": 1_000_000_000.0,
        "gigabyte": 1_000_000_000.0,
        "tb": 1_000_000_000_000.0,
        "terabyte": 1_000_000_000_000.0,
        "kib": 1024.0,
        "kibibyte": 1024.0,
        "mib": 1024.0**2,
        "mebibyte": 1024.0**2,
        "gib": 1024.0**3,
        "gibibyte": 1024.0**3,
        "tib": 1024.0**4,
        "tebibyte": 1024.0**4,
        "bit": 0.125,
        "bits": 0.125,
    },
}

# Temperature units (handled separately because conversion is affine, not a
# pure scaling). Aliases normalise to one of: c, f, k.
_TEMP_ALIASES: dict[str, str] = {
    "c": "c",
    "celsius": "c",
    "centigrade": "c",
    "f": "f",
    "fahrenheit": "f",
    "k": "k",
    "kelvin": "k",
}


def _norm(unit: str) -> str:
    return unit.strip().lower()


def _temp_to_celsius(value: float, unit: str) -> float:
    if unit == "c":
        return value
    if unit == "f":
        return (value - 32.0) * 5.0 / 9.0
    if unit == "k":
        return value - 273.15
    raise ConversionError(f"unknown temperature unit {unit!r}")


def _celsius_to(value_c: float, unit: str) -> float:
    if unit == "c":
        return value_c
    if unit == "f":
        return value_c * 9.0 / 5.0 + 32.0
    if unit == "k":
        return value_c + 273.15
    raise ConversionError(f"unknown temperature unit {unit!r}")


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert ``value`` from ``from_unit`` to ``to_unit``.

    Raises :class:`ConversionError` for unknown units or when the two units
    belong to different categories (e.g. metres to grams).
    """
    fu = _norm(from_unit)
    tu = _norm(to_unit)

    # Temperature first — it has its own (affine) rules.
    if fu in _TEMP_ALIASES or tu in _TEMP_ALIASES:
        if fu not in _TEMP_ALIASES or tu not in _TEMP_ALIASES:
            raise ConversionError(
                "cannot convert between temperature and non-temperature units"
            )
        celsius = _temp_to_celsius(value, _TEMP_ALIASES[fu])
        return _celsius_to(celsius, _TEMP_ALIASES[tu])

    from_cat = _find_category(fu)
    to_cat = _find_category(tu)
    if from_cat is None:
        raise ConversionError(f"unknown unit {from_unit!r}")
    if to_cat is None:
        raise ConversionError(f"unknown unit {to_unit!r}")
    if from_cat != to_cat:
        raise ConversionError(
            f"cannot convert {from_cat} ({from_unit}) to {to_cat} ({to_unit})"
        )
    table = _FACTOR_TABLES[from_cat]
    base = value * table[fu]
    return base / table[tu]


def _find_category(unit: str) -> str | None:
    for category, table in _FACTOR_TABLES.items():
        if unit in table:
            return category
    return None


class ConvertUnitsArgs(BaseModel):
    value: float = Field(description="The numeric quantity to convert.")
    from_unit: str = Field(description="Source unit, e.g. 'km', 'lb', 'celsius'.")
    to_unit: str = Field(description="Target unit, e.g. 'mi', 'kg', 'fahrenheit'.")


class ConvertUnitsTool(BaseTool):
    name = "convert_units"
    description = (
        "Convert a value between units (offline, deterministic). Categories: "
        "length, mass, volume, speed, data, and temperature (C/F/K). "
        "Source and target must be in the same category."
    )
    args_model = ConvertUnitsArgs

    async def run(self, args: ConvertUnitsArgs) -> ToolResult:
        try:
            result = convert(args.value, args.from_unit, args.to_unit)
        except ConversionError as exc:
            return ToolResult(content=f"error: {exc}", is_error=True)
        return ToolResult(
            content=f"{args.value} {args.from_unit} = {result} {args.to_unit}",
            data={
                "value": args.value,
                "from_unit": args.from_unit,
                "to_unit": args.to_unit,
                "result": result,
            },
        )
