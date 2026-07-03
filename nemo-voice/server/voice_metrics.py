"""In-process counters for /health and per-turn latency.

We debugged the wake-word and the "give me a minute" silence blind — there was
no signal for how long replies took or whether a turn ended cleanly. This is the
minimum to stop that: a session counter, a turn counter, and the last reply
latency, all readable at /health and logged per turn. No external deps, no
persistence — it resets on restart, which is fine for a liveness probe.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger("nemo.voice_metrics")

_DB_PATH = Path(
    os.environ.get(
        "VOICE_METRICS_DB", str(Path(__file__).resolve().parent / "voice_metrics.db")
    )
)

_TTFSW_DDL = """
CREATE TABLE IF NOT EXISTS ttfsw (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    ms REAL NOT NULL,
    tier TEXT NOT NULL,
    route TEXT NOT NULL
)
"""


def record_ttfsw_ms(session_id: str, ms: float, tier: str, route: str) -> None:
    """Record TTFSW (time-to-first-spoken-word) in SQLite for the latency dashboard."""
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(_TTFSW_DDL)
            conn.execute(
                "INSERT INTO ttfsw (session_id, ts, ms, tier, route) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, ts, ms, tier, route),
            )
    except sqlite3.Error as exc:
        LOG.warning("voice_metrics: ttfsw insert failed: %s", exc)


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
