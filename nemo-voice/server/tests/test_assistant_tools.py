"""The voice-side calculator / converter / world clock + their wiring."""

from __future__ import annotations

import json

import assistant_tools
import voice_brain


# ── the tools compute correctly ──────────────────────────────────────────────


def test_calculate():
    assert (
        json.loads(assistant_tools.dispatch("calculate", {"expression": "80 * 0.15"}))[
            "result"
        ]
        == 12.0
    )
    assert (
        json.loads(assistant_tools.dispatch("calculate", {"expression": "sqrt(144)"}))[
            "result"
        ]
        == 12.0
    )


def test_calculate_blocks_unsafe():
    out = json.loads(
        assistant_tools.dispatch("calculate", {"expression": "__import__('os')"})
    )
    assert "error" in out
    # nested-power DoS is rejected, not computed
    assert "error" in json.loads(
        assistant_tools.dispatch("calculate", {"expression": "(9**1000)**1000"})
    )


def test_convert_units():
    out = json.loads(
        assistant_tools.dispatch(
            "convert_units", {"value": 5, "from_unit": "miles", "to_unit": "km"}
        )
    )
    assert round(out["result"], 2) == 8.05
    out = json.loads(
        assistant_tools.dispatch(
            "convert_units", {"value": 70, "from_unit": "f", "to_unit": "c"}
        )
    )
    assert round(out["result"], 1) == 21.1


def test_convert_units_rejects_cross_category():
    out = json.loads(
        assistant_tools.dispatch(
            "convert_units", {"value": 1, "from_unit": "kg", "to_unit": "km"}
        )
    )
    assert "error" in out


def test_world_time():
    out = json.loads(assistant_tools.dispatch("world_time", {"timezone": "tokyo"}))
    assert out["zone"] == "Asia/Tokyo" and "time" in out
    assert "error" in json.loads(
        assistant_tools.dispatch("world_time", {"timezone": "Narnia"})
    )


# ── they're actually wired into the voice agent ──────────────────────────────


def test_registered_in_voice_functions():
    names = {f["name"] for f in voice_brain.FUNCTIONS}
    assert {"calculate", "convert_units", "world_time"} <= names


async def test_reachable_through_voice_dispatch():
    # voice_brain.dispatch is the single router the realtime backend calls.
    out = json.loads(await voice_brain.dispatch("calculate", {"expression": "2+2"}))
    assert out["result"] == 4
    out = json.loads(await voice_brain.dispatch("world_time", {"timezone": "utc"}))
    assert out["zone"] == "UTC"
