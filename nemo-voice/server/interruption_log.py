"""Interruption learning — Nemo learns WHEN Avazbek wants to be spoken to.

Every proactive delivery is logged; if he speaks within ENGAGE_WINDOW seconds
of a spoken interruption, it counts as engaged. Sources whose engagement rate
drops below the floor (after enough tries) get demoted: the proactive loop
delivers them as phone pushes instead of speaking up.

Stores event source + decision + outcome only — never the message content.
Table lives in memory_v2.db alongside the other memory tables.
"""

from __future__ import annotations

import logging
import os
import time

import memory_store

LOG = logging.getLogger("nemo.interruption_log")

_ENGAGE_WINDOW_SEC = float(os.environ.get("INTERRUPT_ENGAGE_WINDOW", "60"))
_MIN_SAMPLES = int(os.environ.get("INTERRUPT_MIN_SAMPLES", "4"))
_DEMOTE_BELOW = float(os.environ.get("INTERRUPT_DEMOTE_BELOW", "0.25"))
_RATE_LOOKBACK = 30  # judge each source on its most recent deliveries

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS interruptions ("
    " id INTEGER PRIMARY KEY,"
    " ts REAL NOT NULL,"
    " source TEXT NOT NULL,"
    " decision TEXT NOT NULL,"
    " engaged INTEGER)"
)


def _connect():
    con = memory_store.connect()
    con.execute(_SCHEMA)
    return con


def record_delivery(source: str, decision: str) -> int:
    """Log one delivered proactive event (engaged unknown yet)."""
    con = _connect()
    try:
        cur = con.execute(
            "INSERT INTO interruptions (ts, source, decision) VALUES (?, ?, ?)",
            (time.time(), source, decision),
        )
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def on_user_speech() -> None:
    """He spoke — any spoken interruption in the window counts as engaged."""
    cutoff = time.time() - _ENGAGE_WINDOW_SEC
    con = _connect()
    try:
        con.execute(
            "UPDATE interruptions SET engaged = 1 "
            "WHERE engaged IS NULL AND decision = 'speak' AND ts >= ?",
            (cutoff,),
        )
        # Anything older with no reaction was ignored.
        con.execute(
            "UPDATE interruptions SET engaged = 0 WHERE engaged IS NULL AND ts < ?",
            (cutoff,),
        )
        con.commit()
    finally:
        con.close()


def demoted(source: str) -> bool:
    """Should this source stop SPEAKing and PUSH instead? Needs enough history."""
    con = _connect()
    try:
        rows = con.execute(
            "SELECT engaged FROM interruptions "
            "WHERE source = ? AND decision = 'speak' AND engaged IS NOT NULL "
            "ORDER BY ts DESC LIMIT ?",
            (source, _RATE_LOOKBACK),
        ).fetchall()
    finally:
        con.close()
    if len(rows) < _MIN_SAMPLES:
        return False
    rate = sum(e for (e,) in rows) / len(rows)
    if rate < _DEMOTE_BELOW:
        LOG.info(
            "interruptions: source %r demoted (engagement %.0f%%)", source, rate * 100
        )
        return True
    return False
