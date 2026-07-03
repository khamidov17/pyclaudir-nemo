"""DashScope-realtime-shaped session protocol for the self-hosted gateway.

Speaks exactly the event dialect nemo-voice's qwen_pump already uses, so
cutting over to self-hosted is just QWEN_REALTIME_URL:

client → gateway: session.update, input_audio_buffer.append,
                  conversation.item.create, response.create, response.cancel
gateway → client: session.created, input_audio_buffer.speech_started,
                  conversation.item.input_audio_transcription.completed,
                  response.created, response.audio_transcript.delta,
                  response.audio.delta, response.done, error

The session is a state machine over (VAD, audio buffer, response lifecycle);
model inference lives behind the OmniBackend protocol (see omni_backend.py) so
this file is fully testable with a fake backend.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from collections.abc import AsyncIterator
from typing import Protocol

from vad import EnergyVad

LOG = logging.getLogger("gateway.protocol")


class OmniBackend(Protocol):
    async def transcribe(self, pcm: bytes) -> str:
        """User-audio → transcript text."""
        ...

    def respond(
        self, system_prompt: str, history: list[dict], tools: list[dict]
    ) -> AsyncIterator[dict]:
        """Yield {'text': ...} deltas, then optionally {'function_call': {...}},
        then {'audio': b'...'} chunks for the spoken reply."""
        ...


class GatewaySession:
    """One client connection's realtime state."""

    def __init__(self, backend: OmniBackend) -> None:
        self.backend = backend
        self.vad = EnergyVad()
        self.audio_buf = bytearray()
        self.system_prompt = ""
        self.tools: list[dict] = []
        self.history: list[dict] = []
        self.response_id = 0
        self.response_active = False
        # turn_detection.create_response=false → transcribe but stay silent
        # until an explicit response.create (the ambient-mode contract).
        self.auto_respond = True

    def hello(self) -> dict:
        return {"type": "session.created"}

    async def handle(self, raw: str) -> list[dict]:
        """One client event in → zero or more server events out."""
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return [_error("invalid_json", "event was not valid JSON")]
        etype = event.get("type", "")
        if etype == "session.update":
            return self._session_update(event)
        if etype == "input_audio_buffer.append":
            return await self._audio_append(event)
        if etype == "conversation.item.create":
            return self._item_create(event)
        if etype == "response.create":
            # Explicit generation request (the text-inject / tool-result flow —
            # DashScope generates on this, not just on VAD end-of-turn).
            out = [{"type": "response.created"}]
            out.extend(await self._run_response())
            return out
        if etype == "response.cancel":
            self.response_active = False
            return []
        return [_error("unknown_event", etype)]

    def _session_update(self, event: dict) -> list[dict]:
        session = event.get("session") or {}
        self.system_prompt = session.get("instructions", self.system_prompt)
        self.tools = session.get("tools", self.tools)
        td = session.get("turn_detection")
        if isinstance(td, dict) and "create_response" in td:
            self.auto_respond = bool(td["create_response"])
        return []

    def _item_create(self, event: dict) -> list[dict]:
        """Injected context / tool results become history items."""
        item = event.get("item") or {}
        role = item.get("role", "user")
        parts = item.get("content") or []
        text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
        if item.get("type") == "function_call_output":
            self.history.append({"role": "tool", "content": item.get("output", "")})
        elif text:
            self.history.append({"role": role, "content": text})
        return []

    async def _audio_append(self, event: dict) -> list[dict]:
        try:
            pcm = base64.b64decode(event.get("audio", ""), validate=True)
        except (binascii.Error, ValueError, TypeError):
            return [_error("bad_audio", "audio was not valid base64")]
        self.audio_buf.extend(pcm)
        out: list[dict] = []
        for vad_event in self.vad.feed(pcm):
            if vad_event == "speech_started":
                out.append({"type": "input_audio_buffer.speech_started"})
            elif vad_event == "speech_ended":
                out.extend(await self._end_of_turn())
        return out

    async def _end_of_turn(self) -> list[dict]:
        """User stopped talking: transcribe, run the model, stream the reply."""
        pcm = bytes(self.audio_buf)
        self.audio_buf.clear()
        transcript = (await self.backend.transcribe(pcm)).strip()
        if not transcript:
            return []
        self.history.append({"role": "user", "content": transcript})
        out: list[dict] = [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": transcript,
            }
        ]
        if self.auto_respond:
            out.append({"type": "response.created"})
            out.extend(await self._run_response())
        return out

    async def _run_response(self) -> list[dict]:
        self.response_active = True
        self.response_id += 1
        rid = self.response_id
        out: list[dict] = []
        reply_text: list[str] = []
        async for delta in self.backend.respond(
            self.system_prompt, list(self.history), self.tools
        ):
            if not self.response_active or rid != self.response_id:
                break  # cancelled (barge-in)
            out.extend(self._delta_events(delta, reply_text))
        if reply_text:
            self.history.append({"role": "assistant", "content": "".join(reply_text)})
        out.append({"type": "response.done"})
        self.response_active = False
        return out

    def _delta_events(self, delta: dict, reply_text: list[str]) -> list[dict]:
        if "text" in delta:
            reply_text.append(delta["text"])
            return [{"type": "response.audio_transcript.delta", "delta": delta["text"]}]
        if "audio" in delta:
            b64 = base64.b64encode(delta["audio"]).decode()
            return [{"type": "response.audio.delta", "delta": b64}]
        if "function_call" in delta:
            return [
                {
                    "type": "response.output_item.done",
                    "item": {"type": "function_call", **delta["function_call"]},
                }
            ]
        return []


def _error(code: str, message: str) -> dict:
    return {"type": "error", "error": {"code": code, "message": message}}
