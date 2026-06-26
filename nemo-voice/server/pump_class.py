"""_QwenPump class — extracted from qwen_pump.py to keep files ≤300 lines.

Do not import this module directly; use qwen_pump._QwenPump (re-exported there).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import voice_history
import voice_intent
from qwen_link import QwenLink
from voice_metrics import METRICS

from pump_tools import (
    _SessionCtx,
    _handle_tool,
    _run_bg_tool,
    _track_usage,
)

LOG = logging.getLogger("nemo.qwen_pump")


class _QwenPump:
    """Qwen → App: translate Qwen realtime events into our app protocol."""

    _TURN_IDLE_SEC = 6.0

    def __init__(self, link: QwenLink, ctx: _SessionCtx, orchestrator=None) -> None:
        self.link = link
        self.client_ws = ctx.client_ws
        self.bridge = ctx.bridge
        self._orchestrator = orchestrator
        self.agent_started = False
        self._reply = ""
        self._user_turn_ts: float | None = None
        self._pending_code_intent: str | None = None
        self._pending_search: str | None = None
        self._pending_messages: bool = False
        self._pending_vision: str | None = None
        self._pending_recall: str | None = None
        self._sensitive_response_ids: set[str] = set()
        self._reply_response_id: str | None = None
        self._reply_sensitive: bool = False
        self._turn_timer: asyncio.Task | None = None
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
        elif ev_type == "response.created":
            self._on_response_created(ev)
        elif ev_type == "conversation.item.input_audio_transcription.completed":
            await self._on_user_transcript(ev.get("transcript", ""))
        elif ev_type == "input_audio_buffer.speech_started":
            LOG.info("vad: user speech started")
            if self._orchestrator:
                await self._orchestrator.on_vad_event(self.client_ws)
            await self._barge_in()
        elif ev_type == "response.output_item.done":
            await self._maybe_tool(ev)
        elif ev_type == "response.done":
            await self._done(ev)
        elif ev_type == "error":
            await self._error(ev)

    async def _on_user_transcript(self, transcript: str) -> None:
        LOG.info("user said: %r", transcript)
        voice_history.add("user", transcript)
        if voice_intent.is_deactivate_intent(transcript):
            LOG.info("deactivate on request — session to sleep")
            await self.link.send({"type": "response.cancel"})
            await self._send({"type": "deactivate"})
            await self._send({"type": "user_transcript", "data": transcript})
            return
        if voice_intent.is_record_stop_intent(transcript):
            LOG.info("record stop on request")
            await self._send({"type": "record_stop"})
        elif voice_intent.is_record_start_intent(transcript):
            LOG.info("record start on request")
            await self._send({"type": "record_start", "id": f"rec-{int(time.time())}"})
        self._user_turn_ts = time.monotonic()
        self._arm_intents(transcript)
        await self._send({"type": "user_transcript", "data": transcript})
        if self._orchestrator:
            await self._orchestrator.on_final_transcript(transcript)

    def _arm_intents(self, transcript: str) -> None:
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
        if self._pending_vision:
            self._pending_search = None
        self._pending_recall = (
            transcript if voice_intent.is_record_recall_intent(transcript) else None
        )

    async def _send(self, msg: dict) -> None:
        async with self._send_lock:
            await self.client_ws.send(json.dumps(msg))

    def _on_response_created(self, ev: dict) -> None:
        rid = (ev.get("response") or {}).get("id")
        if rid and self.link.sensitive_next:
            self._sensitive_response_ids.add(rid)
            self.link.sensitive_next = False

    def _reply_is_sensitive(self) -> bool:
        if (
            self._reply_response_id
            and self._reply_response_id in self._sensitive_response_ids
        ):
            return True
        return self._reply_sensitive

    def _reset_reply_sensitivity(self) -> None:
        if self._reply_response_id:
            self._sensitive_response_ids.discard(self._reply_response_id)
        self._reply_response_id = None
        self._reply_sensitive = False

    async def _audio(self, ev: dict) -> None:
        if not self.agent_started:
            self.agent_started = True
            self._reply_response_id = ev.get("response_id")
            if self.link.sensitive_next and not self._reply_response_id:
                self._reply_sensitive = True
                self.link.sensitive_next = False
            self.link.mark_speaking()
            self._log_reply_latency()
            await self._send({"type": "agent_audio_start"})
            if self._orchestrator:
                await self._orchestrator.on_agent_speaking(True)
        await self._send({"type": "audio", "data": ev.get("delta", "")})
        self._arm_turn_timer()

    def _log_reply_latency(self) -> None:
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
        self._turn_timer = None
        try:
            await self._complete_turn(reason="watchdog")
        except Exception as exc:  # noqa: BLE001
            LOG.debug("turn watchdog: %s", exc)

    async def _complete_turn(self, reason: str = "response.done") -> None:
        if self._turn_timer:
            self._turn_timer.cancel()
            self._turn_timer = None
        if self.agent_started:
            self.agent_started = False
            self.link.mark_idle()
            LOG.info("turn: complete (completed_by=%s)", reason)
            await self._send({"type": "turn_complete"})
            if self._orchestrator:
                await self._orchestrator.on_agent_speaking(False)
        if self._reply.strip():
            if self._reply_is_sensitive():
                LOG.info("turn: sensitive reply — not journaled")
            else:
                voice_history.add("nemo", self._reply)
            self._reply = ""
        self._reset_reply_sensitivity()
        self._run_recoveries()

    def _run_recoveries(self) -> None:
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
        self.link.mark_idle()
        if self._reply.strip() and not self._reply_is_sensitive():
            voice_history.add("nemo", self._reply)
        self._reply = ""
        self._reset_reply_sensitivity()
        await self._send({"type": "interrupted", "data": "barge_in"})
        await self.link.send({"type": "response.cancel"})
        if self._orchestrator:
            await self._orchestrator.on_agent_speaking(False)

    async def _maybe_tool(self, ev: dict) -> None:
        item = ev.get("item", {})
        if item.get("type") == "function_call":
            name = item.get("name")
            if name == "delegate_task":
                self._pending_code_intent = None
                self._pending_recall = None
            elif name == "web_search":
                self._pending_search = None
            elif name == "look":
                self._pending_vision = None
            if self._orchestrator:
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                await self._orchestrator.on_tool_call(name or "", args)
            await _handle_tool(self.link, self.bridge, item)

    async def _done(self, ev: dict) -> None:
        await self._complete_turn()
        _track_usage(ev)

    async def _error(self, ev: dict) -> None:
        err = ev.get("error", {})
        LOG.error("Qwen error: %s", err)
        await self._send({"type": "error", "message": err.get("message", "Qwen error")})
