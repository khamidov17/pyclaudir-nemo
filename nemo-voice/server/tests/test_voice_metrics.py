"""voice_metrics: the counters behind /health."""

from __future__ import annotations

from voice_metrics import _Metrics


def test_session_counts_track_active_and_total():
    m = _Metrics()
    m.session_start()
    m.session_start()
    m.session_end()
    snap = m.snapshot()
    assert snap["sessions_active"] == 1
    assert snap["sessions_total"] == 2


def test_session_end_never_goes_negative():
    m = _Metrics()
    m.session_end()
    assert m.snapshot()["sessions_active"] == 0


def test_record_turn_updates_latency_and_count():
    m = _Metrics()
    m.record_turn(420)
    m.record_turn(130)
    snap = m.snapshot()
    assert snap["turns_total"] == 2
    assert snap["last_turn_latency_ms"] == 130


def test_snapshot_is_serializable_and_complete():
    import json

    snap = _Metrics().snapshot()
    json.dumps(snap)  # must be JSON-safe for /health
    for key in ("status", "uptime_sec", "sessions_active", "turns_total"):
        assert key in snap
