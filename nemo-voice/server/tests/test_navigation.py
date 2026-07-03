"""navigation — announcement ladder, arrival, reroute, tool dispatch."""

from __future__ import annotations

import json

import pytest

import navigation
from navigation import Navigator, Step

# ~111m per 0.001 deg latitude at the equator — handy for synthetic positions.
LAT_M = 0.001 / 111.0


def _nav() -> Navigator:
    return Navigator(
        "the clinic",
        [
            Step(lat=0.0100, lon=0.0, instruction="turn right onto Amir Temur"),
            Step(
                lat=0.0200, lon=0.0, instruction="you have arrived at your destination"
            ),
        ],
    )


@pytest.fixture(autouse=True)
def _reset():
    navigation._ACTIVE["nav"] = None
    navigation._ACTIVE["pending_dest"] = None


def test_announcement_ladder():
    nav = _nav()
    assert nav.on_fix(0.0100 - 600 * LAT_M, 0.0) == []  # too far, quiet
    assert nav.on_fix(0.0100 - 450 * LAT_M, 0.0) == [
        "in 500 meters, turn right onto Amir Temur"
    ]
    assert nav.on_fix(0.0100 - 400 * LAT_M, 0.0) == []  # rung already announced
    assert nav.on_fix(0.0100 - 90 * LAT_M, 0.0) == [
        "in 100 meters, turn right onto Amir Temur"
    ]
    assert nav.on_fix(0.0100 - 28 * LAT_M, 0.0) == ["now: turn right onto Amir Temur"]


def test_skipped_rungs_do_not_backfire():
    nav = _nav()
    # First fix already close: only the 'now' announcement, not all three.
    assert nav.on_fix(0.0100 - 28 * LAT_M, 0.0) == ["now: turn right onto Amir Temur"]
    assert nav.on_fix(0.0100 - 27 * LAT_M, 0.0) == []


def test_step_advance_and_arrival():
    nav = _nav()
    nav.on_fix(0.0100 - 10 * LAT_M, 0.0)  # within advance radius → step 2
    assert nav.idx == 1
    out = nav.on_fix(0.0200 - 30 * LAT_M, 0.0)
    assert out == ["you have arrived at the clinic"]
    assert nav.done


def test_offroute_triggers_reroute_when_moving_away():
    nav = _nav()
    nav.on_fix(0.0100 - 300 * LAT_M, 0.0)  # on route, approaching
    # Wrong turn: each fix is further from every remaining maneuver.
    assert nav.on_fix(0.0100 - 500 * LAT_M, 0.0) == []
    assert nav.on_fix(0.0100 - 700 * LAT_M, 0.0) == []
    assert nav.on_fix(0.0100 - 900 * LAT_M, 0.0) == ["rerouting"]


def test_far_but_approaching_is_not_offroute():
    nav = _nav()
    for meters in (2000, 1500, 1000, 700, 600):
        assert nav.on_fix(0.0100 - meters * LAT_M, 0.0) == []
    assert nav.offroute_count == 0


@pytest.mark.asyncio
async def test_dispatch_and_first_fix_builds_route(monkeypatch):
    out = json.loads(
        await navigation.dispatch("start_navigation", {"destination": "clinic"})
    )
    assert "waiting for GPS" in out["status"]
    assert navigation.active()

    async def fake_build(dest, lat, lon):
        return _nav()

    monkeypatch.setattr(navigation, "_build", fake_build)
    first = await navigation.on_location(0.0, 0.0)
    assert first == ["route ready — turn right onto Amir Temur"]
    assert isinstance(navigation._ACTIVE["nav"], Navigator)


@pytest.mark.asyncio
async def test_stop_clears_state():
    await navigation.dispatch("start_navigation", {"destination": "clinic"})
    await navigation.dispatch("stop_navigation", {})
    assert not navigation.active()
    assert await navigation.on_location(0.0, 0.0) == []


@pytest.mark.asyncio
async def test_route_failure_degrades_gracefully(monkeypatch):
    await navigation.dispatch("start_navigation", {"destination": "nowhere"})

    async def boom(dest, lat, lon):
        raise RuntimeError("osrm down")

    monkeypatch.setattr(navigation, "_build", boom)
    out = await navigation.on_location(0.0, 0.0)
    assert out and "couldn't get a route" in out[0]
    assert not navigation.active()


def test_instruction_rendering():
    assert (
        navigation._instruction(
            {"maneuver": {"type": "turn", "modifier": "right"}, "name": "Amir Temur"}
        )
        == "turn right onto Amir Temur"
    )
    assert navigation._instruction({"maneuver": {"type": "arrive"}}) == (
        "you have arrived at your destination"
    )


def test_reset_clears_leaked_route():
    navigation._ACTIVE["nav"] = _nav()
    assert navigation.active()
    navigation.reset()  # session teardown must clear module-global state
    assert not navigation.active()
