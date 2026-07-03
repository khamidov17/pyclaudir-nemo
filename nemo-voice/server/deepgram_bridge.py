"""Deepgram Voice Agent bridge — extracted from streaming_service.py.

Handles one Deepgram session: settings negotiation, mic → Deepgram,
Deepgram audio → client, function calls, barge-in.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

import websockets

import voice_brain
from action_bridge import ActionBridge
from deepgram_settings import build_settings, speak_model

LOG = logging.getLogger("nemo.deepgram_bridge")

DEEPGRAM_URL = "wss://agent.deepgram.com/v1/agent/converse"
DEEPGRAM_AUDIO_DONE_FALLBACK_SEC = float(
    os.environ.get("DEEPGRAM_AUDIO_DONE_FALLBACK_SEC", "1.5")
)


async def _handle_function_calls(event: dict, deepgram_ws, bridge) -> None:
    for fn in event.get("functions", []):
        if not fn.get("client_side", False):
            continue
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        LOG.debug("voice function call: %s (args at DEBUG only)", name)
        content = await voice_brain.dispatch(name, args, bridge)
        resp: dict = {
            "type": "FunctionCallResponse",
            "id": fn.get("id"),
            "name": name,
            "content": content,
        }
        if "thought_signature" in fn:
            resp["thought_signature"] = fn["thought_signature"]
        await deepgram_ws.send(json.dumps(resp))


async def _wait_for_settings_applied(deepgram_ws) -> None:
    while True:
        message = await asyncio.wait_for(deepgram_ws.recv(), timeout=10)
        if isinstance(message, bytes):
            continue
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "SettingsApplied":
            LOG.info("Deepgram settings applied")
            return
        if event.get("type") == "Error":
            raise RuntimeError(
                event.get("description") or event.get("message") or str(event)
            )


async def _recv_client(client_ws, deepgram_ws, bridge) -> None:
    total_audio = 0
    last_log = 0
    async for raw in client_ws:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            LOG.warning("bad JSON from APK")
            continue
        msg_type = data.get("type")
        if msg_type == "action_result":
            bridge.resolve(data)
        elif msg_type == "audio":
            audio = base64.b64decode(data.get("data", ""))
            if audio:
                total_audio += len(audio)
                if total_audio - last_log >= 8000:
                    last_log = total_audio
                    LOG.info("mic audio from APK: %d bytes total", total_audio)
                await deepgram_ws.send(audio)
        elif msg_type == "end":
            await deepgram_ws.send(json.dumps({"type": "KeepAlive"}))
        elif msg_type == "inject":
            text = data.get("text", "")
            if text:
                await deepgram_ws.send(
                    json.dumps({"type": "InjectUserMessage", "content": text})
                )


class _AudioState:
    """Mutable audio-turn state for _recv_deepgram."""

    __slots__ = ("started", "bytes", "timer")

    def __init__(self) -> None:
        self.started = False
        self.bytes = 0
        self.timer: asyncio.Task | None = None


async def _complete_audio_turn(st: _AudioState, client_ws, delay: float = 0.0) -> None:
    if delay:
        await asyncio.sleep(delay)
    if st.started:
        LOG.info("agent audio turn complete: %d bytes", st.bytes)
        await client_ws.send(json.dumps({"type": "turn_complete"}))
        st.started = False
        st.bytes = 0


def _schedule_audio_done(st: _AudioState, client_ws) -> None:
    if st.timer:
        st.timer.cancel()
    st.timer = asyncio.create_task(
        _complete_audio_turn(st, client_ws, DEEPGRAM_AUDIO_DONE_FALLBACK_SEC)
    )


async def _handle_dg_audio(message: bytes, st: _AudioState, client_ws) -> None:
    if not st.started:
        st.started = True
        await client_ws.send(json.dumps({"type": "agent_audio_start"}))
    st.bytes += len(message)
    await client_ws.send(
        json.dumps({"type": "audio", "data": base64.b64encode(message).decode("ascii")})
    )
    _schedule_audio_done(st, client_ws)


class _DgSession:
    """Bundle deepgram_ws + client_ws + bridge to reduce parameter counts."""

    __slots__ = ("dg_ws", "client_ws", "bridge")

    def __init__(self, dg_ws, client_ws, bridge) -> None:
        self.dg_ws = dg_ws
        self.client_ws = client_ws
        self.bridge = bridge


async def _dg_audio_done(event: dict, st: _AudioState, sess: _DgSession) -> None:
    if st.timer:
        st.timer.cancel()
        st.timer = None
    await _complete_audio_turn(st, sess.client_ws)


async def _dg_barge_in(st: _AudioState, sess: _DgSession) -> None:
    if st.timer:
        st.timer.cancel()
        st.timer = None
    st.started = False
    st.bytes = 0
    await sess.client_ws.send(json.dumps({"type": "interrupted", "data": "barge_in"}))


async def _dg_transcript(event: dict, sess: _DgSession) -> None:
    text = event.get("content") or event.get("text") or ""
    role = event.get("role")
    if text:
        msg_type = "user_transcript" if role == "user" else "text"
        await sess.client_ws.send(json.dumps({"type": msg_type, "data": text}))


async def _handle_dg_event(event: dict, st: _AudioState, sess: _DgSession) -> None:
    etype = event.get("type")
    if etype == "AgentAudioDone":
        await _dg_audio_done(event, st, sess)
    elif etype == "UserStartedSpeaking":
        await _dg_barge_in(st, sess)
    elif etype == "ConversationText":
        await _dg_transcript(event, sess)
    elif etype == "FunctionCallRequest":
        await _handle_function_calls(event, sess.dg_ws, sess.bridge)
    elif etype == "Error":
        LOG.error("Deepgram error: %s", event)
        err_msg = event.get("description") or event.get("message") or "Deepgram error"
        await sess.client_ws.send(json.dumps({"type": "error", "message": err_msg}))
    elif etype == "Warning":
        LOG.warning("Deepgram warning: %s", event)
    elif etype in {
        "Welcome",
        "AgentThinking",
        "AgentStartedSpeaking",
        "SettingsApplied",
        "History",
    }:
        LOG.debug("Deepgram event: %s", etype)
    else:
        LOG.debug("Deepgram event payload: %s", event)


async def _recv_deepgram(deepgram_ws, client_ws, bridge) -> None:
    st = _AudioState()
    sess = _DgSession(deepgram_ws, client_ws, bridge)
    async for message in deepgram_ws:
        if isinstance(message, bytes):
            await _handle_dg_audio(message, st, client_ws)
            continue
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            LOG.warning("Deepgram sent non-JSON text: %r", message[:80])
            continue
        await _handle_dg_event(event, st, sess)


async def _keepalive(deepgram_ws) -> None:
    while True:
        await asyncio.sleep(5)
        await deepgram_ws.send(json.dumps({"type": "KeepAlive"}))


async def run_session(client_ws, voice: str | None, *, api_key: str) -> None:
    """Run one Deepgram session for a pre-authenticated client."""
    headers = {"Authorization": f"Token {api_key}"}
    async with websockets.connect(DEEPGRAM_URL, additional_headers=headers) as dg_ws:
        welcome = await asyncio.wait_for(dg_ws.recv(), timeout=8)
        LOG.info(
            "Deepgram welcome: %s",
            welcome[:120] if isinstance(welcome, str) else "binary",
        )
        LOG.info("voice for session: %s", speak_model(voice))
        await dg_ws.send(json.dumps(build_settings(voice)))
        await _wait_for_settings_applied(dg_ws)
        await client_ws.send(json.dumps({"type": "ready"}))
        bridge = ActionBridge(client_ws)
        to_dg = asyncio.create_task(_recv_client(client_ws, dg_ws, bridge))
        to_client = asyncio.create_task(_recv_deepgram(dg_ws, client_ws, bridge))
        keepalive = asyncio.create_task(_keepalive(dg_ws))
        done, pending = await asyncio.wait(
            {to_dg, to_client, keepalive}, return_when=asyncio.FIRST_COMPLETED
        )
        if to_client in done and to_dg not in done:
            try:
                await client_ws.send(
                    json.dumps(
                        {
                            "type": "error",
                            "message": "voice session ended (Deepgram closed)",
                        }
                    )
                )
            except Exception:  # noqa: BLE001
                pass
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
