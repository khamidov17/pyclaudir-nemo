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
import time

import websockets

import qwen_usage
import reminders
import voice_brain
import voice_facts
import voice_history
import voice_intent
from action_bridge import ActionBridge
from qwen_link import QwenLink
from voice_metrics import METRICS

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
            "instructions": voice_brain.build_prompt(seed_history=True),
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
        await _seed_history(qwen)
        await client_ws.send(json.dumps({"type": "ready"}))
        bridge = ActionBridge(client_ws)
        link = QwenLink(qwen)
        to_qwen = asyncio.create_task(_recv_client(client_ws, link, bridge))
        to_client = asyncio.create_task(_QwenPump(link, client_ws, bridge).run())
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
            # Session ending — cancel any in-flight background tools (they must
            # not write into the closing socket), then write any new durable
            # facts to memory (cheap, rate-limited). Never break teardown.
            await link.aclose()
            try:
                await voice_facts.maybe_extract()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("fact extraction failed: %s", exc)


async def _seed_history(qwen) -> None:
    """Rebuild recent conversation as REAL items so a reconnected session has
    genuine momentum — the model continues the dialogue instead of reading a
    transcript and re-greeting. No response is triggered; Nemo replies when the
    user next speaks. Only seeds a fresh (recent) exchange — see recent_items().
    """
    try:
        items = voice_history.recent_items()
    except Exception as exc:  # noqa: BLE001
        LOG.debug("history seed skipped: %s", exc)
        return
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            continue
        is_user = it.get("role") == "user"
        content_type = "input_text" if is_user else "text"
        try:
            await qwen.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user" if is_user else "assistant",
                            "content": [{"type": content_type, "text": text}],
                        },
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001 — degrade to no-seed, never crash
            LOG.debug("history seed stopped: %s", exc)
            return
    if items:
        LOG.info("seeded %d recent turns into the session", len(items))


async def _recv_client(client_ws, link: QwenLink, bridge) -> None:
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
                await link.send({"type": "input_audio_buffer.append", "audio": b64})
        elif msg_type == "inject":
            text = data.get("text", "")
            if text:
                await link.inject_text(text)


class _QwenPump:
    """Qwen → App: translate Qwen realtime events into our app protocol."""

    # If audio has been flowing but neither more audio nor response.done arrives
    # for this long, treat the turn as finished — covers a lost/late
    # response.done on the flaky link so the app's mic isn't stuck muted. Kept
    # generous (6s) so a normal packet gap mid-reply on the lossy link doesn't
    # falsely end the turn and reopen the mic while Nemo is still speaking
    # (response.done loss is rare, so erring long is safe).
    _TURN_IDLE_SEC = 6.0

    def __init__(self, link: QwenLink, client_ws, bridge) -> None:
        self.link = link
        self.client_ws = client_ws
        self.bridge = bridge
        self.agent_started = False
        self._reply = ""  # accumulates Nemo's spoken text for this turn
        # When the user's transcript landed — used to log reply latency (how long
        # Nemo took to start speaking), the number we were debugging blind.
        self._user_turn_ts: float | None = None
        # The user's code request for THIS turn, if any — cleared when the model
        # calls delegate_task; if still set at turn end, the model forgot to run
        # the code and we recover by delegating it ourselves (never twice).
        self._pending_code_intent: str | None = None
        # Same recovery for web lookups — cleared when the model calls web_search;
        # if still set at turn end, the model said "on it" but never searched, so
        # we run the search ourselves and speak the result.
        self._pending_search: str | None = None
        # "check my messages" — recovery-only (read_messages isn't a model tool),
        # so an explicit ask always fires it and it can never fire on its own.
        self._pending_messages: bool = False
        # "look at this" → camera: the user's question, if a vision request.
        self._pending_vision: str | None = None
        # "summarize what we recorded / send the transcript" → delegate to the
        # engine brain (it holds the dated transcript). Recovery-only: the voice
        # model just says "on it"; the engine speaks the content, so a meeting's
        # words never pass through the voice model (no journaling/leak risk).
        self._pending_recall: str | None = None
        # Latched (from link.sensitive_next) the moment THIS reply starts, so a
        # message-summary's privacy flag is bound to the right reply and can't be
        # consumed by an interleaved turn on the full-duplex link.
        self._reply_sensitive: bool = False
        self._turn_timer: asyncio.Task | None = None
        # Serialize sends to the app socket — the turn watchdog runs as its own
        # task and must not interleave WS frames with the main pump.
        self._send_lock = asyncio.Lock()

    async def run(self) -> None:
        try:
            async for raw in self.link:
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
            await self._on_user_transcript(ev.get("transcript", ""))
        elif ev_type == "input_audio_buffer.speech_started":
            LOG.info("vad: user speech started")
            await self._barge_in()
        elif ev_type == "response.output_item.done":
            await self._maybe_tool(ev)
        elif ev_type == "response.done":
            await self._done(ev)
        elif ev_type == "error":
            await self._error(ev)

    async def _on_user_transcript(self, transcript: str) -> None:
        """A finished user turn: deactivate / record controls, then arm the
        per-turn recovery intents and forward the transcript to the app."""
        LOG.info("user said: %r", transcript)
        voice_history.add("user", transcript)
        if voice_intent.is_deactivate_intent(transcript):
            # "shut up / go to sleep" → end the session NOW; the app drops to
            # wake-word-only (local) mode. Cancel any reply so Nemo goes quiet.
            LOG.info("deactivate on request — session to sleep")
            await self.link.send({"type": "response.cancel"})
            await self._send({"type": "deactivate"})
            await self._send({"type": "user_transcript", "data": transcript})
            return
        # Meeting recorder: start/stop are deterministic CONTROLS to the app (the
        # phone owns the mic + foreground recorder). Nemo still voices a brief
        # confirmation (shaped by the system prompt), so don't return.
        if voice_intent.is_record_stop_intent(transcript):
            LOG.info("record stop on request")
            await self._send({"type": "record_stop"})
        elif voice_intent.is_record_start_intent(transcript):
            LOG.info("record start on request")
            await self._send({"type": "record_start", "id": f"rec-{int(time.time())}"})
        self._user_turn_ts = time.monotonic()
        self._arm_intents(transcript)
        await self._send({"type": "user_transcript", "data": transcript})

    def _arm_intents(self, transcript: str) -> None:
        """Set per-turn recovery flags. Reset every turn so a stale request can't
        recover on a later one."""
        self._pending_code_intent = (
            transcript if voice_intent.is_code_intent(transcript) else None
        )
        self._pending_search = (
            transcript if voice_intent.is_search_intent(transcript) else None
        )
        self._pending_messages = voice_intent.is_messages_intent(transcript)
        self._pending_vision = (
            transcript if voice_intent.is_vision_intent(transcript) else None
        )
        if self._pending_vision:  # vision wins over search on any ambiguity
            self._pending_search = None
        self._pending_recall = (
            transcript if voice_intent.is_record_recall_intent(transcript) else None
        )

    async def _send(self, msg: dict) -> None:
        async with self._send_lock:
            await self.client_ws.send(json.dumps(msg))

    async def _audio(self, ev: dict) -> None:
        if not self.agent_started:
            self.agent_started = True
            # Bind the sensitive flag to THIS reply at its first audio, so an
            # interleaved turn can't consume it (privacy: message summaries must
            # never be journaled).
            if self.link.sensitive_next:
                self._reply_sensitive = True
                self.link.sensitive_next = False
            self.link.mark_speaking()  # hold background results until this reply ends
            self._log_reply_latency()
            await self._send({"type": "agent_audio_start"})
        await self._send({"type": "audio", "data": ev.get("delta", "")})
        self._arm_turn_timer()

    def _log_reply_latency(self) -> None:
        """How long after the user's transcript Nemo started speaking."""
        if self._user_turn_ts is None:
            return
        latency_ms = int((time.monotonic() - self._user_turn_ts) * 1000)
        self._user_turn_ts = None
        METRICS.record_turn(latency_ms)
        LOG.info("turn: reply latency %dms", latency_ms)

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
            await self._complete_turn(reason="watchdog")
        except Exception as exc:  # noqa: BLE001 — never crash the watchdog
            LOG.debug("turn watchdog: %s", exc)

    async def _complete_turn(self, reason: str = "response.done") -> None:
        """End the current agent turn exactly once (from response.done OR the
        idle watchdog), telling the app to reopen the mic and saving the reply.
        `reason` is logged so we can see how often the watchdog (a lost/late
        response.done on the flaky link) had to step in."""
        if self._turn_timer:
            self._turn_timer.cancel()
            self._turn_timer = None
        if self.agent_started:
            self.agent_started = False
            self.link.mark_idle()  # reply done → background results may speak now
            LOG.info("turn: complete (completed_by=%s)", reason)
            await self._send({"type": "turn_complete"})
        if self._reply.strip():
            # Privacy: a message-summary reply is NOT journaled — so it's never
            # re-seeded into a future session or fact-extracted. Bound per-reply
            # (latched at this reply's first audio), NOT the session-global flag,
            # so an interleaved turn can't consume it.
            if self._reply_sensitive:
                LOG.info("turn: sensitive reply — not journaled")
            else:
                voice_history.add("nemo", self._reply)
            self._reply = ""
        self._reply_sensitive = False
        self._run_recoveries()

    def _run_recoveries(self) -> None:
        """Fire any must-do tool the model finished the turn without calling.
        Each clears its flag first so it can never run twice."""
        if self._pending_code_intent:
            task, self._pending_code_intent = self._pending_code_intent, None
            LOG.info("recovering missed code delegation")
            self.link.spawn_bg(
                lambda: voice_intent.recover_delegate(self.link, self.bridge, task)
            )
        if self._pending_search:
            query, self._pending_search = self._pending_search, None
            LOG.info("recovering missed web_search: %r", query)
            self.link.spawn_bg(
                lambda: _run_bg_tool(
                    self.link, self.bridge, "web_search", {"query": query}
                )
            )
        # read_messages is NOT a model tool, so this recovery is the ONLY way it
        # fires (explicit-only).
        if self._pending_messages:
            self._pending_messages = False
            LOG.info("fetching messages on explicit request")
            self.link.spawn_bg(
                lambda: _run_bg_tool(self.link, self.bridge, "read_messages", {})
            )
        if self._pending_vision:
            q, self._pending_vision = self._pending_vision, None
            LOG.info("recovering missed look: %r", q)
            self.link.spawn_bg(
                lambda: _run_bg_tool(self.link, self.bridge, "look", {"question": q})
            )
        # Recording recall → the engine brain (it holds the dated transcript).
        if self._pending_recall:
            task, self._pending_recall = self._pending_recall, None
            LOG.info("recovering recording recall → engine: %r", task)
            self.link.spawn_bg(
                lambda: voice_intent.recover_delegate(self.link, self.bridge, task)
            )

    async def _barge_in(self) -> None:
        if not self.agent_started:
            return
        if self._turn_timer:
            self._turn_timer.cancel()
            self._turn_timer = None
        self.agent_started = False
        self.link.mark_idle()  # reply interrupted → background results may speak
        # Persist what Nemo had said so far — a barged-into turn still counts
        # toward memory (don't drop the partial reply) — UNLESS it's a sensitive
        # message summary, which must never be journaled even when barged into.
        if self._reply.strip():
            if not self._reply_sensitive:
                voice_history.add("nemo", self._reply)
            self._reply = ""
        self._reply_sensitive = False
        await self._send({"type": "interrupted", "data": "barge_in"})
        await self.link.send({"type": "response.cancel"})

    async def _maybe_tool(self, ev: dict) -> None:
        item = ev.get("item", {})
        if item.get("type") == "function_call":
            name = item.get("name")
            if name == "delegate_task":
                self._pending_code_intent = None  # model handled it — no recovery
                self._pending_recall = None  # model already delegated the recall
            elif name == "web_search":
                self._pending_search = None  # model searched — no recovery
            elif name == "look":
                self._pending_vision = None  # model looked — no recovery
            await _handle_tool(self.link, self.bridge, item)

    async def _done(self, ev: dict) -> None:
        await self._complete_turn()
        _track_usage(ev)

    async def _error(self, ev: dict) -> None:
        err = ev.get("error", {})
        LOG.error("Qwen error: %s", err)
        await self._send({"type": "error", "message": err.get("message", "Qwen error")})


# Tools that may take a few seconds: run them in the BACKGROUND so Nemo keeps
# talking, then inject the result so he relays it mid-conversation. The link
# caps concurrency ("two background agents") and owns the tasks per session.
_BG_TOOLS = {"web_search", "look"}
# Tools whose RESULT is privacy-sensitive — the summary reply must not be
# journaled / re-uploaded / fact-extracted.
_SENSITIVE_TOOLS = {"read_messages"}


async def _tool_output(link: QwenLink, call_id: str, content: str) -> None:
    """Return a tool result to Qwen and let it speak."""
    await link.respond_to(
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": content,
        }
    )


def _bg_label(name: str, args: dict) -> str:
    """A short human label for a background task, for the spoken hand-back."""
    return str(args.get("query") or args.get("task") or name)[:80]


async def _run_bg_tool(link: QwenLink, bridge, name: str, args: dict) -> None:
    """Run a slow tool off the conversation, then inject its result to speak."""
    sensitive = name in _SENSITIVE_TOOLS
    try:
        content = await voice_brain.dispatch(name, args, bridge)
    except Exception as exc:  # noqa: BLE001
        content = json.dumps({"error": str(exc)})
    # Wait for a gap in the conversation so the result lands naturally.
    await link.wait_until_idle()
    if link.closed:
        # Non-sensitive results survive a reconnect via the engine→phone path.
        # Sensitive results (message content) must NEVER hit the engine/DB — drop.
        if not sensitive:
            _deliver_via_engine(name, args, content)
        return
    if sensitive:
        # The summary reply must not be journaled/re-uploaded/fact-extracted.
        link.sensitive_next = True
        await link.inject_text(
            "[Avazbek asked to check his messages. Give a SHORT, natural, "
            "Jarvis-style rundown — count, who, and the gist, one or two "
            "sentences. If it says 'nothing new', just say there's nothing new. "
            f"Result: {content}]"
        )
        return
    await link.inject_text(
        f"[Your background task '{_bg_label(name, args)}' just finished — tell "
        "Avazbek the answer now, briefly and naturally, one or two sentences. "
        f"Result: {content}]"
    )


def _deliver_via_engine(name: str, args: dict, content: str) -> None:
    """Fallback when the voice session is gone: push the result to the phone as
    an immediate engine notification so a background answer is never lost."""
    try:
        parsed = json.loads(content)
        answer = parsed.get("result") or parsed.get("error") or content
    except Exception:  # noqa: BLE001
        answer = content
    reminders.notify_now(f"About '{_bg_label(name, args)}': {answer}")


async def _handle_tool(link: QwenLink, bridge, item: dict) -> None:
    """Run a tool call and feed the result back so Nemo can keep talking."""
    name = item.get("name", "")
    call_id = item.get("call_id", "")
    try:
        args = json.loads(item.get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    LOG.info("qwen function call: %s %s", name, args)
    if name in _BG_TOOLS:
        # Ack immediately and keep the conversation going; the answer is spoken
        # when the background task finishes.
        await _tool_output(
            link,
            call_id,
            json.dumps(
                {"status": "on it — searching in the background, back in a sec"}
            ),
        )
        link.spawn_bg(lambda: _run_bg_tool(link, bridge, name, args))
        return
    content = await voice_brain.dispatch(name, args, bridge)
    await _tool_output(link, call_id, content)


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
