"""Identity + ambient gates for the Qwen pump — mixin for _QwenPump.

Pure relocation from pump_class.py (which hit the 300-line limit): the
speaker-lock verification, the phone-biometric second factor, and the
ambient-mode turn gate. Expects the host class to provide ``self.link``,
``self.bridge``, ``self._ctx``, ``self._speaker``, and ``self._send``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import ambient
import speaker_gate
import voice_history
from pump_tools import _tool_output

LOG = logging.getLogger("nemo.qwen_pump")


class _GatesMixin:
    # Provided by the host class (_QwenPump) — declared for the type checker.
    link: Any
    bridge: Any
    _ctx: Any
    _speaker: Any

    async def _send(self, msg: dict) -> None: ...  # implemented by host

    async def _set_auto_respond(self, on: bool) -> None:
        """Toggle upstream auto-response (turn_detection.create_response);
        the response.cancel in the store-only path covers backends that
        ignore this setting."""
        await self.link.send(
            {
                "type": "session.update",
                "session": {"turn_detection": {"create_response": on}},
            }
        )

    async def _ambient_gate(self, transcript: str) -> bool:
        """Ambient mode: journal owner speech, reply only when addressed.
        Returns True when this turn is fully handled (store-only)."""
        if not ambient.enabled():
            return False
        if self._ctx.ambient_on and ambient.is_off_intent(transcript):
            self._ctx.ambient_on = False
            LOG.info("ambient mode OFF")
            await self._set_auto_respond(True)
            await self.link.send({"type": "response.create"})  # confirm aloud
            return False
        if not self._ctx.ambient_on:
            if ambient.is_on_intent(transcript):
                self._ctx.ambient_on = True
                LOG.info("ambient mode ON")
                await self._set_auto_respond(False)
            return False
        if ambient.is_addressed(transcript):
            # Addressed by name → answer this one turn, stay ambient.
            await self.link.send({"type": "response.create"})
            return False
        # Store-only: suppress any auto-response, journal VERIFIED speech only.
        # allow_sensitive() (not merely "not stranger") so the UNSURE gray zone
        # isn't stored until the phone biometric confirms it's really him.
        await self.link.send({"type": "response.cancel"})
        await self._send({"type": "interrupted", "data": "ambient_suppress"})
        if self._speaker.allow_sensitive():
            voice_history.add("user", transcript)
        else:
            LOG.info("ambient: unverified speech dropped (not journaled)")
        return True

    async def _verify_speaker(self) -> None:
        """Verify the turn's voice against the owner's voiceprint. Runs in a
        worker thread while Qwen is already generating — no added latency.
        Text injects (no audio) come from the authenticated app: keep verdict."""
        pcm = self._speaker.take_turn_audio()
        if not speaker_gate.enabled() or not pcm:
            return
        verdict = await asyncio.to_thread(speaker_gate.verify, pcm)
        self._speaker.record(verdict)
        LOG.info("speaker gate: %s", verdict.value)
        if verdict is speaker_gate.Verdict.UNSURE and not (
            self._speaker.session_verified or self._speaker.biometric_pending
        ):
            self._speaker.biometric_pending = True
            self.link.spawn_bg(self._biometric_check)

    async def _biometric_check(self) -> None:
        """Voice was borderline (sick/noisy) — second factor via the phone's
        own Face ID / fingerprint prompt."""
        try:
            result = await self.bridge.run("biometric_check")
            if result.get("ok"):
                self._speaker.session_verified = True
                LOG.info("speaker gate: biometric passed — session verified")
        finally:
            self._speaker.biometric_pending = False

    async def _refuse_unverified(self, item: dict) -> None:
        LOG.warning("speaker gate: blocked %s for unverified voice", item.get("name"))
        await _tool_output(
            self.link,
            item.get("call_id", ""),
            json.dumps(
                {
                    "error": "voice not recognized — this is owner-only. "
                    "Politely say you can only do that for Avazbek."
                }
            ),
        )
