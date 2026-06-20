"""Qwen Omni Realtime bridge — native speech-to-speech backend.

Speaks the SAME app protocol as streaming_service / gemini_streaming (app sends
16k PCM audio, receives 24k PCM audio + ready/agent_audio_start/audio/
turn_complete/interrupted/user_transcript/text/error/action), so the Flutter app
needs zero changes. Connects to Alibaba Model Studio's Qwen Omni Realtime
WebSocket (OpenAI-Realtime-beta schema). Reuses voice_brain for Nemo's identity,
memory and tools, and ActionBridge for phone actions. Selected via
VOICE_BACKEND=qwen.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import websockets

import qwen_usage
import voice_brain
import voice_facts
import voice_history
from action_bridge import ActionBridge

LOG = logging.getLogger("nemo.qwen_realtime")

QWEN_URL = os.environ.get(
    "QWEN_REALTIME_URL", "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
)
QWEN_MODEL = os.environ.get("QWEN_REALTIME_MODEL", "qwen3.5-omni-plus-realtime")
# Default voice when the app doesn't pick one. The app's Settings voice picker
# sends one of ALLOWED_VOICES per session.
QWEN_VOICE = os.environ.get("QWEN_VOICE", "Ethan")
# Voices qwen3.5-omni-plus-realtime actually supports (verified by generation;
# Chelsie/Cherry exist only on the older turbo model and 400 here). Whitelist
# so a bad/old pick falls back to the default instead of erroring.
ALLOWED_VOICES = {
    "Ethan",
    "Ryan",
    "Dylan",
    "Aiden",  # male
    "Tina",
    "Serena",
    "Jennifer",
    "Sunny",  # female
}


def _voice_for(voice: str | None) -> str:
    """The app's chosen voice if it's a known one, else the default."""
    return voice if voice in ALLOWED_VOICES else QWEN_VOICE


# Transcription engine for user audio → text (Qwen realtime requires this id).
_TRANSCRIBE_MODEL = os.environ.get("QWEN_TRANSCRIBE_MODEL", "gummy-realtime-v1")


def _tools() -> list[dict]:
    """voice_brain tool schemas in Qwen/OpenAI realtime function format."""
    return [
        {
            "type": "function",
            "name": f["name"],
            "description": f["description"],
            "parameters": f.get("parameters") or {"type": "object", "properties": {}},
        }
        for f in voice_brain.FUNCTIONS
    ]


def _session_config(voice: str) -> dict:
    return {
        "type": "session.update",
        "session": {
            "modalities": ["text", "audio"],
            "voice": voice,
            "instructions": voice_brain.build_prompt(),
            "input_audio_format": "pcm",  # 16kHz mono PCM16 from the app
            "output_audio_format": "pcm",  # 24kHz mono PCM16 to the app
            "input_audio_transcription": {"model": _TRANSCRIBE_MODEL},
            "turn_detection": {
                "type": "server_vad",
                # Lower threshold catches softer/accented speech; longer silence
                # stops Nemo cutting him off when he pauses mid-sentence.
                "threshold": float(os.environ.get("QWEN_VAD_THRESHOLD", "0.35")),
                "silence_duration_ms": int(
                    os.environ.get("QWEN_VAD_SILENCE_MS", "1200")
                ),
            },
            "tools": _tools(),
        },
    }


async def run_session(client_ws, voice: str | None = None) -> None:
    """Bridge one authenticated app client to a Qwen Omni Realtime session."""
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        await client_ws.send(
            json.dumps({"type": "error", "message": "DASHSCOPE_API_KEY not configured"})
        )
        return
    chosen = _voice_for(voice)
    url = f"{QWEN_URL}?model={QWEN_MODEL}"
    headers = {"Authorization": f"Bearer {api_key}"}
    async with websockets.connect(url, additional_headers=headers) as qwen:
        await qwen.recv()  # session.created
        await qwen.send(json.dumps(_session_config(chosen)))
        LOG.info("Qwen realtime session open (model=%s, voice=%s)", QWEN_MODEL, chosen)
        await client_ws.send(json.dumps({"type": "ready"}))
        bridge = ActionBridge(client_ws)
        to_qwen = asyncio.create_task(_recv_client(client_ws, qwen, bridge))
        to_client = asyncio.create_task(_QwenPump(qwen, client_ws, bridge).run())
        try:
            done, pending = await asyncio.wait(
                {to_qwen, to_client}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        finally:
            # Session ending — write any new durable facts to memory (cheap,
            # rate-limited). Never let this break teardown.
            try:
                await voice_facts.maybe_extract()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("fact extraction failed: %s", exc)


async def _recv_client(client_ws, qwen, bridge) -> None:
    """App → Qwen: stream mic audio, resolve tool results, inject text."""
    async for raw in client_ws:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg_type = data.get("type")
        if msg_type == "action_result":
            bridge.resolve(data)
        elif msg_type == "audio":
            b64 = data.get("data", "")
            if b64:
                await qwen.send(
                    json.dumps({"type": "input_audio_buffer.append", "audio": b64})
                )
        elif msg_type == "inject":
            text = data.get("text", "")
            if text:
                await _inject_text(qwen, text)


async def _inject_text(qwen, text: str) -> None:
    await qwen.send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
    )
    await qwen.send(json.dumps({"type": "response.create"}))


class _QwenPump:
    """Qwen → App: translate Qwen realtime events into our app protocol."""

    # If audio has been flowing but neither more audio nor response.done arrives
    # for this long, treat the turn as finished — covers a lost/late
    # response.done on the flaky link so the app's mic isn't stuck muted. Kept
    # generous (6s) so a normal packet gap mid-reply on the lossy link doesn't
    # falsely end the turn and reopen the mic while Nemo is still speaking
    # (response.done loss is rare, so erring long is safe).
    _TURN_IDLE_SEC = 6.0

    def __init__(self, qwen, client_ws, bridge) -> None:
        self.qwen = qwen
        self.client_ws = client_ws
        self.bridge = bridge
        self.agent_started = False
        self._reply = ""  # accumulates Nemo's spoken text for this turn
        self._turn_timer: asyncio.Task | None = None
        # Serialize sends to the app socket — the turn watchdog runs as its own
        # task and must not interleave WS frames with the main pump.
        self._send_lock = asyncio.Lock()

    async def run(self) -> None:
        try:
            async for raw in self.qwen:
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._dispatch(ev)
        finally:
            if self._turn_timer:
                self._turn_timer.cancel()

    async def _dispatch(self, ev: dict) -> None:
        ev_type = ev.get("type")
        if ev_type == "response.audio.delta":
            await self._audio(ev)
        elif ev_type == "response.audio_transcript.delta":
            self._reply += ev.get("delta", "")
            await self._send({"type": "text", "data": ev.get("delta", "")})
        elif ev_type == "conversation.item.input_audio_transcription.completed":
            transcript = ev.get("transcript", "")
            voice_history.add("user", transcript)
            await self._send({"type": "user_transcript", "data": transcript})
        elif ev_type == "input_audio_buffer.speech_started":
            await self._barge_in()
        elif ev_type == "response.output_item.done":
            await self._maybe_tool(ev)
        elif ev_type == "response.done":
            await self._done(ev)
        elif ev_type == "error":
            await self._error(ev)

    async def _send(self, msg: dict) -> None:
        async with self._send_lock:
            await self.client_ws.send(json.dumps(msg))

    async def _audio(self, ev: dict) -> None:
        if not self.agent_started:
            self.agent_started = True
            await self._send({"type": "agent_audio_start"})
        await self._send({"type": "audio", "data": ev.get("delta", "")})
        self._arm_turn_timer()

    def _arm_turn_timer(self) -> None:
        if self._turn_timer:
            self._turn_timer.cancel()
        self._turn_timer = asyncio.create_task(self._turn_timeout())

    async def _turn_timeout(self) -> None:
        try:
            await asyncio.sleep(self._TURN_IDLE_SEC)
        except asyncio.CancelledError:
            return
        # Finished sleeping — clear our own handle BEFORE _complete_turn, or it
        # would cancel the task it's running in and abort the turn_complete send.
        self._turn_timer = None
        try:
            await self._complete_turn()
        except Exception as exc:  # noqa: BLE001 — never crash the watchdog
            LOG.debug("turn watchdog: %s", exc)

    async def _complete_turn(self) -> None:
        """End the current agent turn exactly once (from response.done OR the
        idle watchdog), telling the app to reopen the mic and saving the reply."""
        if self._turn_timer:
            self._turn_timer.cancel()
            self._turn_timer = None
        if self.agent_started:
            self.agent_started = False
            await self._send({"type": "turn_complete"})
        if self._reply.strip():
            voice_history.add("nemo", self._reply)
            self._reply = ""

    async def _barge_in(self) -> None:
        if not self.agent_started:
            return
        if self._turn_timer:
            self._turn_timer.cancel()
            self._turn_timer = None
        self.agent_started = False
        # Persist what Nemo had said so far — a barged-into turn still counts
        # toward memory (don't drop the partial reply).
        if self._reply.strip():
            voice_history.add("nemo", self._reply)
            self._reply = ""
        await self._send({"type": "interrupted", "data": "barge_in"})
        await self.qwen.send(json.dumps({"type": "response.cancel"}))

    async def _maybe_tool(self, ev: dict) -> None:
        item = ev.get("item", {})
        if item.get("type") == "function_call":
            await _handle_tool(self.qwen, self.bridge, item)

    async def _done(self, ev: dict) -> None:
        await self._complete_turn()
        _track_usage(ev)

    async def _error(self, ev: dict) -> None:
        err = ev.get("error", {})
        LOG.error("Qwen error: %s", err)
        await self._send({"type": "error", "message": err.get("message", "Qwen error")})


async def _handle_tool(qwen, bridge, item: dict) -> None:
    """Run a tool call and feed the result back so Nemo can keep talking."""
    name = item.get("name", "")
    call_id = item.get("call_id", "")
    try:
        args = json.loads(item.get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    LOG.info("qwen function call: %s %s", name, args)
    content = await voice_brain.dispatch(name, args, bridge)
    await qwen.send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": content,
                },
            }
        )
    )
    await qwen.send(json.dumps({"type": "response.create"}))


def _track_usage(ev: dict) -> None:
    usage = ev.get("response", {}).get("usage")
    if not usage:
        return
    try:
        total = qwen_usage.record(QWEN_MODEL, usage)
        LOG.info(
            "qwen usage: turn %d | cost so far $%.4f%s",
            total["turns"],
            total["cost_usd"],
            " (preview/free)" if total.get("preview_free") else "",
        )
    except Exception as exc:  # never let accounting kill the turn
        LOG.warning("usage tracking failed: %s", exc)
