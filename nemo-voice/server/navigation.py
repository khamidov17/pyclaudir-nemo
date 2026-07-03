"""Nemo voice guidance — turn-by-turn navigation spoken into the live session.

"Navigate me to X" → geocode (Nominatim) + route (OSRM, keyless) → the app
streams location frames over the authenticated voice websocket → each fix is
measured against the next maneuver and announcements ("in 500 meters, turn
right onto …") are woven into the conversation via the same idle-inject rail
background tools use. See docs/design-translator-nav.md.

Single-user by design: one module-level Navigator at a time. Location frames
are never journaled; state dies with stop()/arrival.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field

import aiohttp

LOG = logging.getLogger("nemo.navigation")

OSRM_URL = os.environ.get("OSRM_URL", "https://router.project-osrm.org")
GEOCODE_URL = os.environ.get(
    "NAV_GEOCODE_URL", "https://nominatim.openstreetmap.org/search"
)
_TIMEOUT = aiohttp.ClientTimeout(total=15)
_UA = {"User-Agent": "nemo-voice-nav/1.0"}

_RUNGS = (500.0, 100.0, 30.0)
_ADVANCE_M = 25.0
_ARRIVE_M = 40.0
_OFFROUTE_M = 120.0
_OFFROUTE_FIXES = 3

FUNCTIONS: list[dict] = [
    {
        "name": "start_navigation",
        "description": (
            "Start spoken turn-by-turn guidance to a destination. Use when "
            "Avazbek asks you to navigate/direct/guide him somewhere."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "Where to go."}
            },
            "required": ["destination"],
        },
    },
    {
        "name": "stop_navigation",
        "description": "Stop the current turn-by-turn guidance.",
        "parameters": {"type": "object", "properties": {}},
    },
]
TOOL_NAMES = {f["name"] for f in FUNCTIONS}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _instruction(step: dict) -> str:
    m = step.get("maneuver") or {}
    kind = m.get("type", "continue")
    modifier = m.get("modifier", "")
    # Road names come from OSM (third-party data) and ride into the prompt —
    # strip anything that could read as a template/injection marker.
    road = re.sub(r"[\[\]<>|{}]", "", step.get("name") or "")[:60]
    if kind == "arrive":
        return "you have arrived at your destination"
    verb = {
        "turn": f"turn {modifier}",
        "new name": "continue straight",
        "depart": "head out",
        "merge": f"merge {modifier}",
        "on ramp": f"take the ramp {modifier}",
        "off ramp": f"take the exit {modifier}",
        "fork": f"keep {modifier} at the fork",
        "roundabout": "take the roundabout",
        "end of road": f"at the end of the road, turn {modifier}",
    }.get(kind, f"{kind} {modifier}".strip())
    return f"{verb} onto {road}" if road else verb


@dataclass
class Step:
    lat: float
    lon: float
    instruction: str
    announced: set[float] = field(default_factory=set)


class Navigator:
    """One active route: consumes location fixes, emits spoken announcements."""

    def __init__(self, destination: str, steps: list[Step]) -> None:
        self.destination = destination
        self.steps = steps
        self.idx = 0
        self.offroute_count = 0
        self.prev_dist: float | None = None
        self.done = False

    def _next(self) -> Step | None:
        return self.steps[self.idx] if self.idx < len(self.steps) else None

    def on_fix(self, lat: float, lon: float) -> list[str]:
        """Announcements for this GPS fix (usually 0 or 1)."""
        step = self._next()
        if step is None or self.done:
            return []
        dist = _haversine_m(lat, lon, step.lat, step.lon)
        if self._is_offroute(lat, lon, dist):
            return ["rerouting"]
        if self.idx == len(self.steps) - 1 and dist <= _ARRIVE_M:
            self.done = True
            return [f"you have arrived at {self.destination}"]
        if dist <= _ADVANCE_M:
            self.idx += 1
            return self.on_fix(lat, lon)
        return self._rung_announcement(step, dist)

    def _rung_announcement(self, step: Step, dist: float) -> list[str]:
        # Tightest rung the fix fits in (28m → "now", not "in 500 meters").
        fitting = [r for r in _RUNGS if dist <= r]
        if not fitting:
            return []
        rung = min(fitting)
        if rung in step.announced:
            return []
        # Mark this rung and every larger one (skipped while approaching fast).
        step.announced.update(r for r in _RUNGS if r >= rung)
        if rung <= 30.0:
            return [f"now: {step.instruction}"]
        return [f"in {int(rung)} meters, {step.instruction}"]

    def _is_offroute(self, lat: float, lon: float, dist_next: float) -> bool:
        """Off-route = repeatedly MOVING AWAY from the next maneuver while far
        from every remaining one. Being far on a long straight is normal."""
        moving_away = self.prev_dist is not None and dist_next > self.prev_dist + 10
        self.prev_dist = dist_next
        remaining = min(
            (_haversine_m(lat, lon, s.lat, s.lon) for s in self.steps[self.idx :]),
            default=dist_next,
        )
        if moving_away and remaining > _OFFROUTE_M:
            self.offroute_count += 1
        else:
            self.offroute_count = 0
        return self.offroute_count >= _OFFROUTE_FIXES


# One user, one active navigation.
_ACTIVE: dict[str, object] = {"nav": None, "pending_dest": None}


async def _geocode(place: str) -> tuple[float, float] | None:
    params = {"q": place, "format": "json", "limit": "1"}
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_UA) as s:
        async with s.get(GEOCODE_URL, params=params) as r:
            data = await r.json(content_type=None)
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])


async def _route(frm: tuple[float, float], to: tuple[float, float]) -> list[Step]:
    url = (
        f"{OSRM_URL}/route/v1/driving/{frm[1]},{frm[0]};{to[1]},{to[0]}"
        "?steps=true&overview=false"
    )
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_UA) as s:
        async with s.get(url) as r:
            data = await r.json(content_type=None)
    routes = data.get("routes") or []
    if not routes:
        return []
    steps = []
    for leg in routes[0].get("legs", []):
        for raw in leg.get("steps", []):
            loc = (raw.get("maneuver") or {}).get("location") or [0, 0]
            steps.append(Step(lat=loc[1], lon=loc[0], instruction=_instruction(raw)))
    return steps


async def dispatch(name: str, args: dict) -> str:
    if name == "stop_navigation":
        _ACTIVE["nav"] = None
        _ACTIVE["pending_dest"] = None
        return json.dumps({"status": "navigation stopped"})
    dest = (args.get("destination") or "").strip()
    if not dest:
        return json.dumps({"error": "no destination given"})
    _ACTIVE["nav"] = None
    _ACTIVE["pending_dest"] = dest
    return json.dumps({"status": f"starting guidance to {dest} — waiting for GPS fix"})


async def _first_fix(dest: str, lat: float, lon: float) -> list[str]:
    try:
        nav = await _build(dest, lat, lon)
    except Exception as exc:  # noqa: BLE001 — guidance must not kill the ws
        LOG.warning("route fetch failed: %s", exc)
        _ACTIVE["pending_dest"] = None
        return ["I couldn't get a route — try again or use Maps"]
    _ACTIVE["pending_dest"] = None
    if nav is None:
        return [f"I couldn't find {dest} on the map"]
    _ACTIVE["nav"] = nav
    first = nav.steps[0].instruction if nav.steps else ""
    return [f"route ready — {first}"]


async def on_location(lat: float, lon: float) -> list[str]:
    """Feed one GPS fix; returns announcements to speak. Builds the route on
    the first fix after start_navigation (needs the start position)."""
    dest = _ACTIVE["pending_dest"]
    if dest is not None:
        return await _first_fix(str(dest), lat, lon)
    nav = _ACTIVE["nav"]
    if not isinstance(nav, Navigator) or nav.done:
        return []
    announcements = nav.on_fix(lat, lon)
    if "rerouting" in announcements:
        _ACTIVE["pending_dest"] = nav.destination
        _ACTIVE["nav"] = None
    if nav.done:
        _ACTIVE["nav"] = None
    return announcements


async def _build(dest: str, lat: float, lon: float) -> Navigator | None:
    to = await _geocode(dest)
    if to is None:
        return None
    steps = await _route((lat, lon), to)
    if not steps:
        return None
    return Navigator(dest, steps)


def active() -> bool:
    return _ACTIVE["nav"] is not None or _ACTIVE["pending_dest"] is not None


def reset() -> None:
    """Clear all navigation state — called on session teardown so a route can't
    leak into the next session (a mobile reconnect starts a fresh session while
    _ACTIVE is module-global)."""
    _ACTIVE["nav"] = None
    _ACTIVE["pending_dest"] = None
