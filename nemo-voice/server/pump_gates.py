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
import narration
import speaker_gate
import subtitles
import translator
import voice_brain
import voice_history
from pump_tools import _tool_output

LOG = logging.getLogger("nemo.qwen_pump")


class _GatesMixin:
    # Provided by the host class (_QwenPump) — declared for the type checker.
    link: Any
    bridge: Any
    _ctx: Any
    _speaker: Any
    _reply: str

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
            voice_history.add("user", transcript, speaker=self._speaker.name or "")
        else:
            LOG.info("ambient: unverified speech dropped (not journaled)")
        return True

    async def _translator_on(self, lang: str) -> None:
        self._ctx.translator_lang = lang
        LOG.info("translator mode ON → %s", lang)
        await self._set_auto_respond(False)
        await self.link.send(
            {
                "type": "session.update",
                "session": {"instructions": translator.instructions(lang)},
            }
        )
        await self.link.inject_text(
            "[Mode change: confirm in Avazbek's language, in a few "
            f"words, that you are now interpreting {lang}.]"
        )

    async def _translator_off(self) -> None:
        LOG.info("translator mode OFF")
        self._ctx.translator_lang = None
        self._ctx.translator_aside = False
        await self._set_auto_respond(True)
        await self.link.send(
            {
                "type": "session.update",
                "session": {"instructions": voice_brain.build_prompt(True)},
            }
        )
        await self.link.inject_text(
            "[Mode change: interpreting is over — confirm briefly, back "
            "to normal assistant.]"
        )

    async def _check_media_modes(self, transcript: str) -> None:
        """Flip scene-narration / subtitles modes on spoken toggles. Does not
        suppress the turn — the model still confirms conversationally."""
        ctx = self._ctx
        if not ctx.narrating and narration.on_intent(transcript):
            ctx.narrating = True
            narration.reset()
            await self._send({"type": "narration_start"})
        elif ctx.narrating and narration.is_off_intent(transcript):
            ctx.narrating = False
            await self._send({"type": "narration_stop"})
        if not ctx.subtitles and subtitles.on_intent(transcript):
            ctx.subtitles = True
        elif ctx.subtitles and subtitles.is_off_intent(transcript):
            ctx.subtitles = False

    async def _emit_subtitle(self, who: str, text: str) -> None:
        if self._ctx.subtitles and text.strip():
            await self._send({"type": "subtitle", "who": who, "text": text.strip()})

    async def _translator_gate(self, transcript: str) -> bool:
        """Interpreter mode: every turn gets a direction hint, nothing more.
        Returns True when this turn was handled as translation/aside."""
        ctx = self._ctx
        if ctx.translator_lang is None:
            lang = translator.on_intent(transcript)
            if lang:
                await self._translator_on(lang)
                return True
            return False
        if translator.is_off_intent(transcript):
            await self._translator_off()
            return True
        verdict = self._speaker.last_verdict
        if verdict is speaker_gate.Verdict.OFF:
            # No voiceprint (gate off/unavailable) — we can't tell who's speaking,
            # so don't force the owner direction. Pass owner_voice=None for a
            # neutral hint that leans on the language rule in the prompt.
            ctx.translator_aside = ambient.is_addressed(transcript)
            owner_voice: bool | None = None
        else:
            owner_voice = verdict is speaker_gate.Verdict.OWNER
            ctx.translator_aside = owner_voice and ambient.is_addressed(transcript)
        hint = translator.turn_hint(
            ctx.translator_lang, owner_voice=owner_voice, aside=ctx.translator_aside
        )
        await self.link.inject_text(hint)
        return True

    async def _verify_speaker(self) -> None:
        """Identify the turn's voice (owner / enrolled guest / stranger). Runs
        in a worker thread while Qwen is already generating — no added latency.
        Text injects (no audio) come from the authenticated app: keep verdict."""
        pcm = self._speaker.take_turn_audio()
        if not speaker_gate.enabled() or not pcm:
            return
        if await self._maybe_enroll_guest(pcm):
            return
        verdict, name = await asyncio.to_thread(speaker_gate.identify, pcm)
        self._speaker.record(verdict, name)
        LOG.info("speaker gate: %s (%s)", verdict.value, name or "?")
        if verdict is speaker_gate.Verdict.UNSURE and not (
            self._speaker.session_verified or self._speaker.biometric_pending
        ):
            self._speaker.biometric_pending = True
            self.link.spawn_bg(self._biometric_check)

    async def _maybe_enroll_guest(self, pcm: bytes) -> bool:
        """An armed enrollment ('remember Aziz's voice') captures the next
        NON-owner turn as that person's voiceprint."""
        name = self._speaker.pending_enroll
        if not name:
            return False
        verdict, _ = await asyncio.to_thread(speaker_gate.identify, pcm)
        if verdict is speaker_gate.Verdict.OWNER:
            self._speaker.record(verdict, "Avazbek")
            return False  # owner still talking — keep waiting for the guest
        self._speaker.pending_enroll = None
        ok = await asyncio.to_thread(speaker_gate.enroll_guest, name, pcm)
        self._speaker.record(verdict, name if ok else None)
        LOG.info("guest enrollment %s: %s", "done" if ok else "failed", name)
        await self.link.inject_text(
            f"[Voice enrollment {'succeeded' if ok else 'failed — audio too short'} "
            f"for {name}. Tell Avazbek briefly.]"
        )
        return True

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
