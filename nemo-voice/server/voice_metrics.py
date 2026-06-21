"""In-process counters for /health and per-turn latency.

We debugged the wake-word and the "give me a minute" silence blind — there was
no signal for how long replies took or whether a turn ended cleanly. This is the
minimum to stop that: a session counter, a turn counter, and the last reply
latency, all readable at /health and logged per turn. No external deps, no
persistence — it resets on restart, which is fine for a liveness probe.
"""

from __future__ import annotations

import time


class _Metrics:
    def __init__(self) -> None:
        self._start = time.time()
        self.sessions_active = 0
        self.sessions_total = 0
        self.turns_total = 0
        self.last_turn_latency_ms = 0

    def session_start(self) -> None:
        self.sessions_active += 1
        self.sessions_total += 1

    def session_end(self) -> None:
        self.sessions_active = max(0, self.sessions_active - 1)

    def record_turn(self, latency_ms: int) -> None:
        self.turns_total += 1
        self.last_turn_latency_ms = latency_ms

    def snapshot(self) -> dict:
        return {
            "status": "ok",
            "uptime_sec": round(time.time() - self._start),
            "sessions_active": self.sessions_active,
            "sessions_total": self.sessions_total,
            "turns_total": self.turns_total,
            "last_turn_latency_ms": self.last_turn_latency_ms,
        }


METRICS = _Metrics()
