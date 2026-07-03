"""ambient — toggles, addressing, and privacy (stranger speech never stored)."""

from __future__ import annotations

import pytest

import ambient


def test_toggle_intents():
    assert ambient.is_on_intent("switch to ambient mode please")
    assert ambient.is_on_intent("shunchaki eshit meni")
    assert ambient.is_off_intent("okay ambient off")
    assert ambient.is_off_intent("normal rejim")
    assert not ambient.is_on_intent("the room ambience is nice")
    assert not ambient.is_off_intent("that's not normal behavior")


def test_addressing():
    assert ambient.is_addressed("Nemo, eshityapsanmi?")
    assert ambient.is_addressed("what do you think nemo")
    assert not ambient.is_addressed("we should call him tomorrow")


def test_enabled_flag(monkeypatch):
    monkeypatch.setenv("VOICE_AMBIENT", "0")
    assert not ambient.enabled()
    monkeypatch.setenv("VOICE_AMBIENT", "1")
    assert ambient.enabled()


def test_custom_names(monkeypatch):
    monkeypatch.setattr(ambient, "_NAMES", ["jarvis"])
    assert ambient.is_addressed("jarvis, lights")
    assert not ambient.is_addressed("nemo, lights")


def _ambient_pump(monkeypatch, tmp_path):
    """A pump wired to fakes with ambient enabled; returns (pump, ctx, taps)."""
    monkeypatch.setenv("VOICE_AMBIENT", "1")
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    import pump_class
    import voice_history
    from pump_tools import _SessionCtx

    taps = {"link": [], "journaled": []}
    monkeypatch.setattr(
        voice_history, "add", lambda r, t, speaker="": taps["journaled"].append(t)
    )
    monkeypatch.setattr(pump_class.interruption_log, "on_user_speech", lambda: None)

    class FakeLink:
        async def send(self, msg):
            taps["link"].append(msg)

    class FakeWs:
        async def send(self, raw):
            pass

    ctx = _SessionCtx(client_ws=FakeWs(), bridge=None)
    return pump_class._QwenPump(FakeLink(), ctx), ctx, taps


@pytest.mark.asyncio
async def test_ambient_store_only_and_privacy(monkeypatch, tmp_path):
    """Owner speech journaled + suppressed; stranger speech never stored."""
    import speaker_gate

    pump, ctx, taps = _ambient_pump(monkeypatch, tmp_path)
    await pump._on_user_transcript("ambient mode please")
    assert ctx.ambient_on

    ctx.speaker.record(speaker_gate.Verdict.OWNER)
    await pump._on_user_transcript("bugun klinikaga borishim kerak")
    assert taps["journaled"][-1] == "bugun klinikaga borishim kerak"
    assert {"type": "response.cancel"} in taps["link"]

    before = len(taps["journaled"])
    ctx.speaker.record(speaker_gate.Verdict.STRANGER)
    await pump._on_user_transcript("men uning do'stiman")
    assert len(taps["journaled"]) == before


@pytest.mark.asyncio
async def test_ambient_addressed_and_toggle_off(monkeypatch, tmp_path):
    """Addressed-by-name turns answer; toggle-off restores normal mode."""
    import speaker_gate

    pump, ctx, taps = _ambient_pump(monkeypatch, tmp_path)
    await pump._on_user_transcript("ambient mode please")
    ctx.speaker.record(speaker_gate.Verdict.OWNER)

    await pump._on_user_transcript("nemo, bugun ob-havo qanday?")
    assert {"type": "response.cancel"} not in taps["link"]
    assert {"type": "response.create"} in taps["link"]

    await pump._on_user_transcript("ambient off, gaplashamiz")
    assert not ctx.ambient_on
