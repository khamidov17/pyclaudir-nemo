"""Voice-side calculator, unit converter, and world clock.

These mirror the engine tools (pyclaudir/tools/{calculate,convert_units,
world_time}.py) but live here so the realtime voice agent can call them
DIRECTLY — fast, offline, no engine round-trip. The voice server is
deliberately self-contained (stdlib only), so the small, stable logic is
ported rather than imported across the package boundary.

Exposes the same shape as the other voice tool modules: FUNCTIONS (schemas),
TOOL_NAMES, and dispatch(name, args) -> JSON string.
"""

from __future__ import annotations

import ast
import json
import math
import operator
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ── calculator ───────────────────────────────────────────────────────────────

_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS: dict[str, Callable[..., Any]] = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
}
_MAX_POW_EXPONENT = 1000
_MAX_RESULT_BITS = 4096  # block nested-power blowups, e.g. (9**1000)**1000


class _CalcError(ValueError):
    pass


def _guard_size(value: Any) -> Any:
    if isinstance(value, int) and value.bit_length() > _MAX_RESULT_BITS:
        raise _CalcError("result too large")
    return value


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _CalcError("only numbers are allowed")
        return node.value
    if isinstance(node, ast.BinOp):
        fn = _BIN_OPS.get(type(node.op))
        if fn is None:
            raise _CalcError("operator not allowed")
        left, right = _eval(node.left), _eval(node.right)
        if type(node.op) is ast.Pow and isinstance(right, (int, float)):
            if abs(right) > _MAX_POW_EXPONENT:
                raise _CalcError("exponent too large")
        return _guard_size(fn(left, right))
    if isinstance(node, ast.UnaryOp):
        fn = _UNARY_OPS.get(type(node.op))
        if fn is None:
            raise _CalcError("unary operator not allowed")
        return fn(_eval(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise _CalcError("only direct calls to allowed functions")
        fn = _FUNCS.get(node.func.id)
        if fn is None:
            raise _CalcError(f"function {node.func.id!r} not allowed")
        return fn(*[_eval(a) for a in node.args])
    raise _CalcError(f"{type(node).__name__} not allowed")


def _calculate(expression: str) -> str:
    expr = (expression or "").strip()
    if not expr:
        return json.dumps({"error": "empty expression"})
    try:
        tree = ast.parse(expr, mode="eval")
        result = _eval(tree)
    except _CalcError as exc:
        return json.dumps({"error": str(exc)})
    except ZeroDivisionError:
        return json.dumps({"error": "division by zero"})
    except (SyntaxError, ValueError, OverflowError, TypeError) as exc:
        return json.dumps({"error": str(exc)})
    if not isinstance(result, (int, float)) or isinstance(result, bool):
        return json.dumps({"error": "did not evaluate to a number"})
    return json.dumps({"expression": expr, "result": result})


# ── unit conversion ──────────────────────────────────────────────────────────

_FACTORS: dict[str, dict[str, float]] = {
    "length": {
        "mm": 0.001, "millimeter": 0.001, "cm": 0.01, "centimeter": 0.01,
        "m": 1.0, "meter": 1.0, "metre": 1.0, "km": 1000.0, "kilometer": 1000.0,
        "kilometre": 1000.0, "in": 0.0254, "inch": 0.0254, "inches": 0.0254,
        "ft": 0.3048, "foot": 0.3048, "feet": 0.3048, "yd": 0.9144, "yard": 0.9144,
        "mi": 1609.344, "mile": 1609.344, "miles": 1609.344, "nmi": 1852.0,
    },
    "mass": {
        "mg": 0.001, "g": 1.0, "gram": 1.0, "grams": 1.0, "kg": 1000.0,
        "kilogram": 1000.0, "kilograms": 1000.0, "t": 1_000_000.0, "tonne": 1_000_000.0,
        "oz": 28.349523125, "ounce": 28.349523125, "lb": 453.59237, "lbs": 453.59237,
        "pound": 453.59237, "pounds": 453.59237, "st": 6350.29318, "stone": 6350.29318,
    },
    "volume": {
        "ml": 0.001, "milliliter": 0.001, "l": 1.0, "liter": 1.0, "litre": 1.0,
        "liters": 1.0, "litres": 1.0, "tsp": 0.00492892159375,
        "tbsp": 0.01478676478125, "floz": 0.0295735295625, "cup": 0.2365882365,
        "cups": 0.2365882365, "pt": 0.473176473, "pint": 0.473176473,
        "qt": 0.946352946, "quart": 0.946352946, "gal": 3.785411784,
        "gallon": 3.785411784, "gallons": 3.785411784,
    },
    "speed": {
        "mps": 1.0, "m/s": 1.0, "kph": 0.277777777778, "km/h": 0.277777777778,
        "kmh": 0.277777777778, "mph": 0.44704, "mi/h": 0.44704, "fps": 0.3048,
        "ft/s": 0.3048, "knot": 0.514444444444, "knots": 0.514444444444,
        "kn": 0.514444444444,
    },
    "data": {
        "b": 1.0, "byte": 1.0, "bytes": 1.0, "kb": 1000.0, "mb": 1_000_000.0,
        "gb": 1_000_000_000.0, "tb": 1_000_000_000_000.0, "kib": 1024.0,
        "mib": 1024.0**2, "gib": 1024.0**3, "tib": 1024.0**4, "bit": 0.125,
        "bits": 0.125,
    },
}
_TEMP = {"c": "c", "celsius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f",
         "k": "k", "kelvin": "k"}


def _to_c(v: float, u: str) -> float:
    return v if u == "c" else (v - 32.0) * 5.0 / 9.0 if u == "f" else v - 273.15


def _from_c(v: float, u: str) -> float:
    return v if u == "c" else v * 9.0 / 5.0 + 32.0 if u == "f" else v + 273.15


def _category(unit: str) -> str | None:
    for cat, table in _FACTORS.items():
        if unit in table:
            return cat
    return None


def _convert(value: float, from_unit: str, to_unit: str) -> str:
    fu, tu = (from_unit or "").strip().lower(), (to_unit or "").strip().lower()
    if fu in _TEMP or tu in _TEMP:
        if fu not in _TEMP or tu not in _TEMP:
            return json.dumps({"error": "can't mix temperature and other units"})
        result = _from_c(_to_c(value, _TEMP[fu]), _TEMP[tu])
        return json.dumps({"result": result, "from": from_unit, "to": to_unit})
    fc, tc = _category(fu), _category(tu)
    if fc is None:
        return json.dumps({"error": f"unknown unit {from_unit!r}"})
    if tc is None:
        return json.dumps({"error": f"unknown unit {to_unit!r}"})
    if fc != tc:
        return json.dumps({"error": f"can't convert {fc} to {tc}"})
    result = value * _FACTORS[fc][fu] / _FACTORS[fc][tu]
    return json.dumps({"result": result, "from": from_unit, "to": to_unit})


# ── world clock ──────────────────────────────────────────────────────────────

_CITY_ALIASES: dict[str, str] = {
    "utc": "UTC", "gmt": "UTC", "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "london": "Europe/London", "uk": "Europe/London", "paris": "Europe/Paris",
    "berlin": "Europe/Berlin", "moscow": "Europe/Moscow", "dubai": "Asia/Dubai",
    "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "india": "Asia/Kolkata",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "china": "Asia/Shanghai",
    "singapore": "Asia/Singapore", "hongkong": "Asia/Hong_Kong",
    "sydney": "Australia/Sydney", "auckland": "Pacific/Auckland",
    "new_york": "America/New_York", "newyork": "America/New_York",
    "nyc": "America/New_York", "chicago": "America/Chicago",
    "los_angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "sf": "America/Los_Angeles", "toronto": "America/Toronto",
}


def _world_time(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return json.dumps({"error": "which city or timezone?"})
    iana = _CITY_ALIASES.get(raw.lower().replace(" ", "_"), raw)
    try:
        zone = ZoneInfo(iana)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return json.dumps({"error": f"unknown timezone or city {name!r}"})
    local = datetime.now(timezone.utc).astimezone(zone)
    return json.dumps({
        "zone": str(zone),
        "time": local.strftime("%A, %H:%M"),
        "datetime": local.strftime("%Y-%m-%d %H:%M:%S"),
        "utc_offset": local.strftime("%z"),
    })


# ── voice tool surface ───────────────────────────────────────────────────────

FUNCTIONS: list[dict] = [
    {
        "name": "calculate",
        "description": (
            "Do exact arithmetic offline. Use this for ANY math — percentages, "
            "tips, square roots, big multiplications — instead of working it out "
            "in your head. Supports + - * / // % ** parentheses and "
            "sqrt/abs/round/min/max."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The arithmetic expression, e.g. '80 * 0.15'.",
                }
            },
            "required": ["expression"],
        },
    },
    {
        "name": "convert_units",
        "description": (
            "Convert between units offline (length, mass, volume, speed, data, "
            "and temperature C/F/K). Use for 'how many km in 5 miles', "
            "'10 lbs to kg', '70 F in C'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "The amount to convert."},
                "from_unit": {"type": "string", "description": "Source unit, e.g. 'miles'."},
                "to_unit": {"type": "string", "description": "Target unit, e.g. 'km'."},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
    },
    {
        "name": "world_time",
        "description": (
            "Get the current time in a city or timezone, offline. Use for "
            "'what time is it in Tokyo'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "A city ('tokyo', 'new york') or IANA name ('Asia/Tokyo').",
                }
            },
            "required": ["timezone"],
        },
    },
]

TOOL_NAMES = {f["name"] for f in FUNCTIONS}


def dispatch(name: str, args: dict) -> str:
    """Run an assistant tool; always returns a JSON string for the voice agent."""
    if name == "calculate":
        return _calculate(args.get("expression", ""))
    if name == "convert_units":
        try:
            value = float(args.get("value"))
        except (TypeError, ValueError):
            return json.dumps({"error": "value must be a number"})
        return _convert(value, args.get("from_unit", ""), args.get("to_unit", ""))
    if name == "world_time":
        return _world_time(args.get("timezone", ""))
    return json.dumps({"error": f"unknown tool {name}"})
