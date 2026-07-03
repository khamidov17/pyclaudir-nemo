"""Intent arming + recovery for the Qwen pump — mixin for _QwenPump.

Pure relocation from pump_class.py (300-line limit): when the model misses an
explicit spoken request (code delegation, search, messages, vision, recording
recall), these recoveries fire it after the turn completes. Expects the host
class to provide ``self.link``, ``self.bridge``, ``self._orchestrator``,
``self._speaker``, and the ``_pending_*`` attributes.
"""

from __future__ import annotations

import logging
from typing import Any

import voice_intent
from pump_tools import _run_bg_tool

LOG = logging.getLogger("nemo.qwen_pump")


class _IntentsMixin:
    # Provided by the host class (_QwenPump) — declared for the type checker.
    link: Any
    bridge: Any
    _orchestrator: Any
    _speaker: Any
    _pending_code_intent: str | None
    _pending_search: str | None
    _pending_messages: bool
    _pending_vision: str | None
    _pending_recall: str | None

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

    def _clear_pending_for(self, name: str | None) -> None:
        if name == "delegate_task":
            self._pending_code_intent = None
            self._pending_recall = None
        elif name == "web_search":
            self._pending_search = None
        elif name == "look":
            self._pending_vision = None

    def _run_recoveries(self) -> None:
        orch = self._orchestrator
        if self._pending_code_intent:
            task, self._pending_code_intent = self._pending_code_intent, None
            LOG.info("recovering missed code delegation")
            self.link.spawn_bg(
                lambda _t=task, _o=orch: voice_intent.recover_delegate(
                    self.link, self.bridge, _t, _o
                )
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
            self._recover_messages()
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
                lambda _t=task, _o=orch: voice_intent.recover_delegate(
                    self.link, self.bridge, _t, _o
                )
            )

    def _recover_messages(self) -> None:
        if not self._speaker.allow_sensitive():
            LOG.warning("speaker gate: blocked messages recovery for unverified voice")
            return
        LOG.info("fetching messages on explicit request")
        self.link.spawn_bg(
            lambda: _run_bg_tool(self.link, self.bridge, "read_messages", {})
        )
