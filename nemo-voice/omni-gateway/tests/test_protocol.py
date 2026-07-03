"""GatewaySession — full turn lifecycle against a fake backend."""

from __future__ import annotations

import base64
import json
import struct

import pytest

from protocol import GatewaySession
from vad import VadConfig


class FakeBackend:
    def __init__(self, reply: list[dict] | None = None):
        self.reply = reply or [{"text": "salom!"}, {"audio": b"\x01\x02"}]
        self.transcripts: list[bytes] = []

    async def transcribe(self, pcm: bytes) -> str:
        self.transcripts.append(pcm)
        return "qalaysan Nemo"

    async def respond(self, system_prompt, history, tools):
        self.seen_prompt = system_prompt
        self.seen_history = history
        for d in self.reply:
            yield d


def _session(backend=None) -> GatewaySession:
    s = GatewaySession(backend or FakeBackend())
    s.vad.cfg = VadConfig(energy_threshold=500.0, start_frames=2, end_ms=40)
    return s


def _pcm(loud: bool, ms: int, rate: int = 16000) -> bytes:
    n = rate * ms // 1000
    val = 3000 if loud else 0
    return struct.pack(f"{n}h", *([val] * n))


def _audio_event(pcm: bytes) -> str:
    return json.dumps(
        {"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}
    )


@pytest.mark.asyncio
async def test_full_turn():
    backend = FakeBackend()
    s = _session(backend)
    assert s.hello() == {"type": "session.created"}
    await s.handle(
        json.dumps(
            {
                "type": "session.update",
                "session": {
                    "instructions": "You are Nemo",
                    "tools": [{"name": "recall"}],
                },
            }
        )
    )
    started = await s.handle(_audio_event(_pcm(loud=True, ms=100)))
    assert {"type": "input_audio_buffer.speech_started"} in started
    ended = await s.handle(_audio_event(_pcm(loud=False, ms=100)))
    types = [e["type"] for e in ended]
    assert "conversation.item.input_audio_transcription.completed" in types
    assert "response.created" in types
    assert "response.audio_transcript.delta" in types
    assert "response.audio.delta" in types
    assert types[-1] == "response.done"
    assert backend.seen_prompt == "You are Nemo"
    assert backend.seen_history[-1] == {"role": "user", "content": "qalaysan Nemo"}


@pytest.mark.asyncio
async def test_transcript_and_audio_payloads():
    s = _session()
    await s.handle(_audio_event(_pcm(loud=True, ms=100)))
    events = await s.handle(_audio_event(_pcm(loud=False, ms=100)))
    tr = next(e for e in events if e["type"].endswith("transcription.completed"))
    assert tr["transcript"] == "qalaysan Nemo"
    audio = next(e for e in events if e["type"] == "response.audio.delta")
    assert base64.b64decode(audio["delta"]) == b"\x01\x02"


@pytest.mark.asyncio
async def test_function_call_delta():
    backend = FakeBackend(
        reply=[
            {"function_call": {"name": "recall", "arguments": "{}", "call_id": "c1"}}
        ]
    )
    s = _session(backend)
    await s.handle(_audio_event(_pcm(loud=True, ms=100)))
    events = await s.handle(_audio_event(_pcm(loud=False, ms=100)))
    item = next(e for e in events if e["type"] == "response.output_item.done")["item"]
    assert item["type"] == "function_call" and item["name"] == "recall"


@pytest.mark.asyncio
async def test_item_create_feeds_history():
    s = _session()
    await s.handle(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "context"}],
                },
            }
        )
    )
    assert s.history == [{"role": "user", "content": "context"}]
    await s.handle(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "output": '{"ok": 1}'},
            }
        )
    )
    assert s.history[-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_bad_input_yields_error_events():
    s = _session()
    assert (await s.handle("not json"))[0]["type"] == "error"
    assert (await s.handle(json.dumps({"type": "nope"})))[0]["type"] == "error"
    bad = json.dumps({"type": "input_audio_buffer.append", "audio": "!!!"})
    assert (await s.handle(bad))[0]["type"] == "error"


@pytest.mark.asyncio
async def test_silent_turn_produces_nothing(monkeypatch):
    backend = FakeBackend()

    async def empty(pcm):
        return "   "

    backend.transcribe = empty
    s = _session(backend)
    await s.handle(_audio_event(_pcm(loud=True, ms=100)))
    events = await s.handle(_audio_event(_pcm(loud=False, ms=100)))
    assert all(e["type"] != "response.created" for e in events)
