"""vLLM-backed OmniBackend — Qwen3-Omni thinker + a TTS sidecar, over HTTP.

Runs against standard OpenAI-compatible endpoints on ava-gpu:

* CHAT_URL  — vLLM serving Qwen3-Omni-30B-A3B (audio-in via input_audio
  content parts, text out, tool calls). Also used for transcription: the
  end-of-turn audio goes through the same model with a transcribe instruction.
* TTS_URL   — /v1/audio/speech-compatible endpoint (Qwen3-TTS / XTTS / any
  server that returns raw PCM16) for the spoken reply.

Everything is plain aiohttp; no GPU code here. Deploying the actual models on
ava-gpu is a separate ops step — this client works against whatever serves
those two URLs.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import AsyncIterator

import aiohttp

LOG = logging.getLogger("gateway.backend")

CHAT_URL = os.environ.get("OMNI_CHAT_URL", "http://127.0.0.1:8000/v1/chat/completions")
TTS_URL = os.environ.get("OMNI_TTS_URL", "http://127.0.0.1:8001/v1/audio/speech")
MODEL = os.environ.get("OMNI_MODEL", "Qwen/Qwen3-Omni-30B-A3B-Instruct")
TTS_VOICE = os.environ.get("OMNI_TTS_VOICE", "default")
_TIMEOUT = aiohttp.ClientTimeout(total=60)

_TRANSCRIBE_PROMPT = (
    "Transcribe this audio exactly as spoken (English/Uzbek/Russian, possibly "
    "mixed). Reply with ONLY the transcript, nothing else."
)


def _audio_part(pcm: bytes) -> dict:
    b64 = base64.b64encode(pcm).decode()
    return {
        "type": "input_audio",
        "input_audio": {"data": b64, "format": "pcm16"},
    }


class VllmOmniBackend:
    """OmniBackend over vLLM chat completions + a PCM TTS endpoint."""

    async def transcribe(self, pcm: bytes) -> str:
        body = {
            "model": MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _TRANSCRIBE_PROMPT},
                        _audio_part(pcm),
                    ],
                }
            ],
            "max_tokens": 300,
            "temperature": 0,
        }
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.post(CHAT_URL, json=body) as r:
                data = await r.json()
        return data["choices"][0]["message"]["content"]

    async def respond(
        self, system_prompt: str, history: list[dict], tools: list[dict]
    ) -> AsyncIterator[dict]:
        """Stream text deltas + tool calls from vLLM, then TTS the full reply."""
        reply: list[str] = []
        async for delta in self._stream_chat(system_prompt, history, tools):
            if "text" in delta:
                reply.append(delta["text"])
            yield delta
        text = "".join(reply).strip()
        if text:
            audio = await self._tts(text)
            if audio:
                yield {"audio": audio}

    async def _stream_chat(
        self, system_prompt: str, history: list[dict], tools: list[dict]
    ) -> AsyncIterator[dict]:
        body = {
            "model": MODEL,
            "messages": [{"role": "system", "content": system_prompt}, *history],
            "stream": True,
            "temperature": 0.7,
        }
        if tools:
            body["tools"] = [{"type": "function", "function": f} for f in tools]
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.post(CHAT_URL, json=body) as r:
                async for raw in r.content:
                    for delta in _parse_sse_line(raw):
                        yield delta

    async def _tts(self, text: str) -> bytes:
        body = {
            "input": text,
            "voice": TTS_VOICE,
            "response_format": "pcm",
        }
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
                async with s.post(TTS_URL, json=body) as r:
                    if r.status != 200:
                        LOG.warning(
                            "TTS returned %d — reply will be text-only", r.status
                        )
                        return b""
                    return await r.read()
        except aiohttp.ClientError as exc:
            LOG.warning("TTS unreachable (%s) — reply will be text-only", exc)
            return b""


def _parse_sse_line(raw: bytes) -> list[dict]:
    """One SSE line from vLLM → gateway deltas ({'text'} / {'function_call'})."""
    line = raw.decode(errors="replace").strip()
    if not line.startswith("data:"):
        return []
    payload = line[5:].strip()
    if payload == "[DONE]":
        return []
    try:
        chunk = json.loads(payload)
        delta = chunk["choices"][0].get("delta") or {}
    except (json.JSONDecodeError, LookupError, TypeError):
        return []
    out: list[dict] = []
    if delta.get("content"):
        out.append({"text": delta["content"]})
    for call in delta.get("tool_calls") or []:
        fn = call.get("function") or {}
        if fn.get("name"):
            out.append(
                {
                    "function_call": {
                        "name": fn["name"],
                        "arguments": fn.get("arguments", "{}"),
                        "call_id": call.get("id", ""),
                    }
                }
            )
    return out
