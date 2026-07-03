"""Local e2e stack pieces — scripted gateway, server boot, fake phone client.

Used by harness_local_e2e.py. The gateway runs IN-PROCESS so each phase can
script the next transcript/reply on the shared backend; the voice server runs
as a real subprocess exactly as systemd would start it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
GATEWAY_DIR = SERVER_DIR.parent / "omni-gateway"
sys.path.insert(0, str(GATEWAY_DIR))

GW_PORT = 8790
VOICE_PORT = 3092
TOKEN = "harness-token"

# 1x1 white PNG for camera action replies.
TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class ScriptedBackend:
    """Gateway brain with per-phase scripts. Defaults: transcribe a fixed
    line, echo the last history item — so unscripted turns still round-trip."""

    def __init__(self) -> None:
        self.transcripts: deque[str] = deque()
        self.replies: deque[list[dict]] = deque()

    async def transcribe(self, pcm: bytes) -> str:
        return self.transcripts.popleft() if self.transcripts else "harness audio turn"

    async def respond(self, system_prompt: str, history: list, tools: list):
        if self.replies:
            for delta in self.replies.popleft():
                yield delta
            return
        last = str(history[-1]["content"]) if history else ""
        yield {"text": f"tushundim: {last[:800]}"}
        yield {"audio": b"\x00\x01" * 160}


async def run_gateway(backend: ScriptedBackend):
    import websockets
    from protocol import GatewaySession

    async def handle(ws):
        session = GatewaySession(backend)
        await ws.send(json.dumps(session.hello()))
        async for raw in ws:
            for event in await session.handle(raw):
                await ws.send(json.dumps(event))

    return await websockets.serve(handle, "127.0.0.1", GW_PORT)


def start_voice_server(data_dir: str) -> subprocess.Popen:
    utc_hour = time.gmtime().tm_hour
    env = {
        **os.environ,
        "VOICE_BACKEND": "qwen",
        "QWEN_REALTIME_URL": f"ws://127.0.0.1:{GW_PORT}",
        "DASHSCOPE_API_KEY": "harness-dummy",
        "NEMO_APP_TOKEN": TOKEN,
        "VOICE_INTERNAL_TOKEN": "harness-internal",
        "NEMO_VOICE_DATA_DIR": data_dir,
        "VOICE_MEMORY_V2": "1",
        "VOICE_ORCHESTRATOR": "1",
        "VOICE_WEAVE_IN": "1",
        "VOICE_PROACTIVE_V2": "1",
        "VOICE_PROACTIVE_POLL_SEC": "2",
        # Pin "local" time to midday so quiet hours never defer the proactive
        # phase regardless of when the harness runs.
        "NEMO_UTC_OFFSET": str(12 - utc_hour),
        "VOICE_AMBIENT": "1",
        "VOICE_SPEAKER_LOCK": "1",
        "SPEAKER_EMBEDDER": "energy",
        "VOICE_PORT": str(VOICE_PORT),
        "VOICE_HTTP_PORT": str(VOICE_PORT + 1),
        "VOICE_TLS_CERT": "",
        "VOICE_TLS_KEY": "",
    }
    return subprocess.Popen(
        [sys.executable, "streaming_service.py"],
        cwd=str(SERVER_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def loud_pcm(ms: int, rate: int = 16000, amp: int = 3000) -> bytes:
    n = rate * ms // 1000
    return struct.pack(f"{n}h", *([amp] * n))


def silent_pcm(ms: int, rate: int = 16000) -> bytes:
    n = rate * ms // 1000
    return struct.pack(f"{n}h", *([0] * n))


class PhoneClient:
    """Acts like the Android app: auth handshake, mic audio, text inject, and
    auto-answers `action` frames (notifications buffer + camera snap)."""

    def __init__(self) -> None:
        self.ws = None
        self.events: list[dict] = []
        self.actions: list[dict] = []

    async def __aenter__(self) -> PhoneClient:
        import websockets

        self.ws = await websockets.connect(f"ws://127.0.0.1:{VOICE_PORT}")
        await self.ws.send(
            json.dumps({"type": "auth", "token": TOKEN, "device_id": ""})
        )
        await self.wait_for("ready", timeout=15)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.ws.close()

    async def _pump_one(self, timeout: float) -> dict | None:
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout)
        except asyncio.TimeoutError:
            return None
        ev = json.loads(raw)
        self.events.append(ev)
        if ev.get("type") == "action":
            self.actions.append(ev)
            await self.ws.send(json.dumps(self._action_reply(ev)))
        return ev

    def _action_reply(self, ev: dict) -> dict:
        buffer = [
            {"app": "Telegram", "from": "Aziz", "text": "kechqurun futbol bormi?"},
            {"app": "WhatsApp", "from": "Mom", "text": "call me when free"},
        ]
        return {
            "type": "action_result",
            "id": ev.get("id"),
            "ok": True,
            "text": json.dumps(buffer),
            "image_b64": TINY_PNG,
        }

    async def wait_for(self, ev_type: str, timeout: float = 10) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ev = await self._pump_one(min(2.0, deadline - time.monotonic()))
            if ev and ev.get("type") == ev_type:
                return ev
        return None

    async def drain(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            await self._pump_one(min(1.0, deadline - time.monotonic()))

    async def inject(self, text: str) -> None:
        await self.ws.send(json.dumps({"type": "inject", "text": text}))

    async def speak_turn(self, amp: int = 3000) -> None:
        """Stream one spoken 'utterance': ~300ms voice then ~900ms silence.
        `amp` is the 'voice' — the energy embedder maps different amplitudes
        to different speakers (3000 = enrolled owner in the harness)."""
        for chunk in (loud_pcm(600, amp=amp), silent_pcm(900)):
            b64 = base64.b64encode(chunk).decode()
            await self.ws.send(json.dumps({"type": "audio", "data": b64}))

    def texts(self) -> str:
        return "".join(
            e.get("data", "") for e in self.events if e.get("type") == "text"
        )

    def types(self) -> list[str]:
        return [e.get("type") for e in self.events]
