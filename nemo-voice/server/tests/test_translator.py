"""translator — toggles, language parsing, direction hints, aside detection."""

from __future__ import annotations

import pytest

import translator


def test_on_intent_with_language():
    assert translator.on_intent("translator mode for chinese") == "Chinese (Mandarin)"
    assert translator.on_intent("tarjimon rejimi rus tiliga") == "Russian"
    assert translator.on_intent("switch to interpreter mode korean") == "Korean"


def test_on_intent_defaults_and_negatives():
    assert translator.on_intent("translator mode") == "Chinese (Mandarin)"
    assert translator.on_intent("what does translation mean?") is None
    assert translator.on_intent("hi") is None


def test_off_intent():
    assert translator.is_off_intent("okay stop translating")
    assert translator.is_off_intent("tarjima tugadi rahmat")
    assert not translator.is_off_intent("don't stop now")


def test_instructions_carry_both_directions():
    text = translator.instructions("Chinese (Mandarin)")
    assert "Chinese (Mandarin)" in text
    assert "NEVER" in text  # no own commentary
    assert "ASIDE" in text  # the one exception is spelled out


def test_turn_hints():
    owner = translator.turn_hint("Chinese (Mandarin)", owner_voice=True, aside=False)
    assert "Chinese (Mandarin)" in owner and "Avazbek speaking" in owner
    other = translator.turn_hint("Chinese (Mandarin)", owner_voice=False, aside=False)
    assert "Other speaker" in other and "Do not answer" in other
    aside = translator.turn_hint("Chinese (Mandarin)", owner_voice=True, aside=True)
    assert "ASIDE" in aside and "Do NOT translate" in aside


def _translator_pump(monkeypatch, tmp_path):
    """Pump wired to fakes; returns (pump, ctx, injected, updates)."""
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    import pump_class
    import voice_history
    from pump_tools import _SessionCtx

    injected, updates = [], []
    monkeypatch.setattr(voice_history, "add", lambda r, t, speaker="": None)
    monkeypatch.setattr(pump_class.interruption_log, "on_user_speech", lambda: None)

    class FakeLink:
        async def send(self, msg):
            if msg.get("type") == "session.update":
                updates.append(msg["session"])

        async def inject_text(self, text):
            injected.append(text)

    class FakeWs:
        async def send(self, raw):
            pass

    ctx = _SessionCtx(client_ws=FakeWs(), bridge=None)
    return pump_class._QwenPump(FakeLink(), ctx), ctx, injected, updates


@pytest.mark.asyncio
async def test_translator_toggle_on_off(monkeypatch, tmp_path):
    pump, ctx, _injected, updates = _translator_pump(monkeypatch, tmp_path)
    await pump._on_user_transcript("translator mode for chinese")
    assert ctx.translator_lang == "Chinese (Mandarin)"
    assert any("INTERPRETER" in u.get("instructions", "") for u in updates)
    assert any(
        u.get("turn_detection", {}).get("create_response") is False for u in updates
    )
    await pump._on_user_transcript("okay stop translating")
    assert ctx.translator_lang is None
    assert any(
        u.get("turn_detection", {}).get("create_response") is True for u in updates
    )


@pytest.mark.asyncio
async def test_translator_hints_per_speaker_and_aside(monkeypatch, tmp_path):
    import speaker_gate

    pump, ctx, injected, _updates = _translator_pump(monkeypatch, tmp_path)
    await pump._on_user_transcript("translator mode for chinese")

    ctx.speaker.record(speaker_gate.Verdict.OWNER)
    await pump._on_user_transcript("bu narsa qancha turadi?")
    assert "Avazbek speaking" in injected[-1]

    ctx.speaker.record(speaker_gate.Verdict.STRANGER)
    await pump._on_user_transcript("这个一百块")
    assert "Other speaker" in injected[-1]

    ctx.speaker.record(speaker_gate.Verdict.OWNER)
    await pump._on_user_transcript("nemo, is that a fair price?")
    assert "ASIDE" in injected[-1]
    assert ctx.translator_aside
