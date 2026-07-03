"""interrupt_policy + watchers + proactive_loop — routing, dedup, delivery."""

from __future__ import annotations

import pytest

import interrupt_policy
import memory_store
import proactive_loop
import session_registry
import watchers
from interrupt_policy import Decision, decide


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    return tmp_path


# ── interrupt_policy ────────────────────────────────────────────────────────


def test_daytime_live_session_speaks():
    assert decide("normal", live_session=True, hour=14) is Decision.SPEAK
    assert decide("high", live_session=True, hour=14) is Decision.SPEAK


def test_daytime_no_session_pushes():
    assert decide("normal", live_session=False, hour=14) is Decision.PUSH


def test_low_severity_pushes_not_defers():
    # low → a quiet push, never a spoken interrupt, and never DEFER-forever.
    assert decide("low", live_session=True, hour=14) is Decision.PUSH
    assert decide("low", live_session=False, hour=14) is Decision.PUSH
    # still deferred inside quiet hours (not critical)
    assert decide("low", live_session=True, hour=2) is Decision.DEFER


def test_quiet_hours_defer_except_critical():
    assert decide("high", live_session=True, hour=2) is Decision.DEFER
    assert decide("critical", live_session=False, hour=2) is Decision.PUSH


def test_unknown_severity_fails_closed_to_push():
    assert decide("weird", live_session=True, hour=14) is Decision.PUSH


# ── watchers ────────────────────────────────────────────────────────────────


def test_followup_watcher_fires_only_when_due():
    memory_store.add_followup("call the clinic", "2020-01-01 10:00")
    memory_store.add_followup("far future", "2099-01-01 10:00")
    memory_store.add_followup("no due date", None)
    events = watchers.poll_followups()
    assert len(events) == 1
    assert "call the clinic" in events[0].text


def test_followup_ack_stops_refire():
    memory_store.add_followup("call the clinic", "2020-01-01 10:00")
    (event,) = watchers.poll_followups()
    watchers.ack(event)
    assert watchers.poll_followups() == []


def test_infra_watcher_reads_new_errors(_data_dir):
    journal = _data_dir / "nemo_error_log.md"
    journal.write_text(
        "## 2026-07-03\n"
        "- `10:00:00 UTC` **[WARN]** `tool/x` — degraded\n"
        "- `10:01:00 UTC` **[ERROR]** `tool/set_alarm` — no clock app installed\n"
    )
    events = watchers.poll_infra()
    assert len(events) == 1
    assert events[0].severity == "high"
    assert "set_alarm" in events[0].text
    watchers.ack(events[0])
    assert watchers.poll_infra() == []


def test_broken_watcher_does_not_kill_poll(monkeypatch):
    def boom():
        raise RuntimeError("watcher broke")

    monkeypatch.setattr(watchers, "WATCHERS", (("bad", boom, lambda e: None),))
    assert watchers.poll_all() == []


# ── proactive_loop ──────────────────────────────────────────────────────────


class _FakeOrch:
    def __init__(self):
        self.spoken: list[str] = []

    async def on_background_chunk(self, chunk, final, rev):
        self.spoken.append(chunk)


@pytest.mark.asyncio
async def test_tick_speaks_into_live_session(monkeypatch):
    memory_store.add_followup("call the clinic", "2020-01-01 10:00")
    orch = _FakeOrch()
    monkeypatch.setattr(session_registry, "any_active", lambda: orch)
    monkeypatch.setattr(interrupt_policy, "local_hour", lambda: 14)
    delivered = await proactive_loop.tick()
    assert delivered == 1
    assert "call the clinic" in orch.spoken[0]
    assert memory_store.open_followups() == []  # acked


@pytest.mark.asyncio
async def test_tick_pushes_without_session(monkeypatch):
    memory_store.add_followup("call the clinic", "2020-01-01 10:00")
    pushed: list[str] = []
    monkeypatch.setattr(session_registry, "any_active", lambda: None)
    monkeypatch.setattr(interrupt_policy, "local_hour", lambda: 14)
    monkeypatch.setattr(
        proactive_loop.reminders, "notify_now", lambda text: pushed.append(text)
    )
    delivered = await proactive_loop.tick()
    assert delivered == 1 and "call the clinic" in pushed[0]


@pytest.mark.asyncio
async def test_deferred_event_not_acked(monkeypatch):
    memory_store.add_followup("call the clinic", "2020-01-01 10:00")
    monkeypatch.setattr(session_registry, "any_active", lambda: None)
    monkeypatch.setattr(interrupt_policy, "local_hour", lambda: 2)  # quiet hours
    delivered = await proactive_loop.tick()
    assert delivered == 0
    assert len(memory_store.open_followups()) == 1  # still open, refires later


@pytest.mark.asyncio
async def test_dropped_weave_in_falls_back_to_push(monkeypatch):
    """If the live session drops the chunk (sensitive turn / closed), the event
    must PUSH, not be acked-and-lost."""
    memory_store.add_followup("call the clinic", "2020-01-01 10:00")

    class DropOrch:
        async def on_background_chunk(self, chunk, final, rev):
            return False  # weave-in dropped it (e.g. sensitive turn)

    pushed: list[str] = []
    monkeypatch.setattr(session_registry, "any_active", lambda: DropOrch())
    monkeypatch.setattr(interrupt_policy, "local_hour", lambda: 14)
    monkeypatch.setattr(
        proactive_loop.reminders, "notify_now", lambda text: pushed.append(text)
    )
    delivered = await proactive_loop.tick()
    assert delivered == 1 and "call the clinic" in pushed[0]  # pushed, not lost
    assert memory_store.open_followups() == []  # acked only after real delivery
