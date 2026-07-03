"""subtitles — toggle intents + pump emit on transcript and reply."""

from __future__ import annotations

import pytest

import subtitles


def test_toggle_intents():
    assert subtitles.on_intent("subtitles on")
    assert subtitles.on_intent("caption this")
    assert subtitles.on_intent("subtitrni yoq")
    assert subtitles.is_off_intent("subtitles off")
    assert subtitles.is_off_intent("stop captioning")
    assert not subtitles.on_intent("nice caption")


@pytest.mark.asyncio
def _subtitle_pump(monkeypatch, tmp_path):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    import pump_class
    import voice_history
    from pump_tools import _SessionCtx

    sent = []
    monkeypatch.setattr(voice_history, "add", lambda *a, **k: None)
    monkeypatch.setattr(pump_class.interruption_log, "on_user_speech", lambda: None)

    class FakeLink:
        async def send(self, msg):
            pass

    class FakeWs:
        async def send(self, raw):
            import json

            sent.append(json.loads(raw))

    ctx = _SessionCtx(client_ws=FakeWs(), bridge=None)
    return pump_class._QwenPump(FakeLink(), ctx), ctx, sent


async def test_pump_emits_subtitles(monkeypatch, tmp_path):
    pump, ctx, sent = _subtitle_pump(monkeypatch, tmp_path)
    # Off by default: no subtitle for a normal turn.
    await pump._on_user_transcript("salom")
    assert not any(e["type"] == "subtitle" for e in sent)

    # Turn on, then a user turn emits a caption.
    await pump._on_user_transcript("subtitles on")
    assert ctx.subtitles
    sent.clear()
    await pump._on_user_transcript("qalaysan")
    subs = [e for e in sent if e["type"] == "subtitle"]
    assert subs and subs[0] == {"type": "subtitle", "who": "you", "text": "qalaysan"}

    # Nemo's reply is captioned on turn completion.
    sent.clear()
    pump._reply = "yaxshi, rahmat"
    pump.agent_started = False
    await pump._complete_turn()
    assert {"type": "subtitle", "who": "nemo", "text": "yaxshi, rahmat"} in sent
