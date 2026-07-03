"""Orchestrator — P1/P2/P3 coordinator between Qwen pump and the engine.

Owns:
- StateSnapshot (one per session)
- routing decisions (VOICE_SHARED_CONTEXT)
- weave-in of engine background chunks (VOICE_WEAVE_IN)

The pump creates one Orchestrator per session; events flow through the
callbacks below. All methods are called from the same asyncio task, so no
locking is needed except around `link` sends (already serialized by QwenLink).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import capabilities
import session_registry
from qwen_link import QwenLink
from semantic_router import RouteDecision, route_tier
from state_snapshot import StateSnapshot

LOG = logging.getLogger("nemo.orchestrator")

_SHARED_CTX = os.environ.get("VOICE_SHARED_CONTEXT", "0").strip() == "1"
_WEAVE_IN = os.environ.get("VOICE_WEAVE_IN", "0").strip() == "1"
_THINKER = os.environ.get("VOICE_THINKER", "0").strip() == "1"

# Self-barge detector (P4)
_BARGE_THRESHOLD = int(os.environ.get("BARGE_IN_SELF_THRESHOLD", "3"))
_BARGE_WINDOW_SEC = 10.0

# Max stashed chunks before we drop the oldest on overflow.
_STASH_MAX_CHUNKS = 3


def _text_item(chunk: str) -> dict:
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": chunk}],
        },
    }


class Orchestrator:
    """Coordinator between QwenLink (voice) and engine (background tasks)."""

    def __init__(self, link: QwenLink, session_id: str) -> None:
        self.link = link
        self.session_id = session_id
        self.snapshot = StateSnapshot(session_id=session_id)
        self._speaking = False
        self._stash: list[tuple[str, int]] = []  # (text, rev) chunks parked on barge-in
        self._barge_ts: list[float] = []  # P4: recent barge timestamps
        self._m2_task: asyncio.Task[None] | None = None
        session_registry.register(session_id, self)
        LOG.info("orchestrator: session %s started", session_id[:8])

    # ── pump callbacks ────────────────────────────────────────────────────────

    async def on_final_transcript(self, text: str) -> RouteDecision:
        """Called when the user's utterance is finalized (VAD done)."""
        self.snapshot.reset_turn()
        self.snapshot.set_transcript(text)
        decision = route_tier(text)
        if _SHARED_CTX:
            self.snapshot.set_route(decision.value)
        if _THINKER and decision != RouteDecision.TIER0_VOICE:
            rev = self.snapshot.rev
            self._m2_task = asyncio.create_task(self._m2_think(text, rev))
        LOG.debug("orchestrator: route=%s for %r", decision.value, text[:60])
        return decision

    async def _m2_think(self, transcript: str, rev: int) -> None:
        """M2 Thinker: async Haiku call during VAD silence to pre-think the answer."""
        import haiku_reasoner

        try:
            # Every capability with a context fetcher contributes (shared
            # memory today; reminders/messages can register theirs later).
            memories = await asyncio.wait_for(
                capabilities.context_for(transcript), timeout=0.5
            )
        except asyncio.TimeoutError:
            memories = []
        if self.snapshot.rev != rev:
            return  # topic changed (barge-in) — discard stale result
        mem_ctx = "\n".join(f"- {m}" for m in memories)
        prompt = (
            f"User said: {transcript}\n"
            f"Context from memory:\n{mem_ctx}\n"
            "In 1-2 short sentences, what is the most helpful thing to say first? "
            "Be direct, no filler."
        )
        result = await haiku_reasoner.call(prompt, max_tokens=80, timeout=1.5)
        if result and self.snapshot.rev == rev:
            self.snapshot.set_thinker_result(result)
            LOG.debug("orchestrator: m2 thinker result stored (%d chars)", len(result))

    async def on_agent_speaking(self, speaking: bool) -> None:
        """Called when Nemo starts or stops speaking audio."""
        self._speaking = speaking
        if not speaking and self._stash:
            await self._flush_stash()

    async def on_tool_call(self, name: str, args: dict) -> None:
        """Pump notifies us of every Qwen function call (for telemetry/context)."""
        if not _SHARED_CTX:
            return
        self.snapshot.add_entity(name, str(args)[:200])

    async def on_background_chunk(self, chunk: str, final: bool, rev: int) -> None:
        """P3: engine POSTed a clause chunk to /internal/brain_result.

        Drop stale chunks (topic changed) or park on active barge-in.
        Otherwise inject into the idle Qwen session so Nemo speaks it.
        """
        if not _WEAVE_IN:
            return
        if rev < self.snapshot.rev:
            LOG.debug(
                "orchestrator: dropping stale chunk (rev=%d < snap=%d)",
                rev,
                self.snapshot.rev,
            )
            return
        if self.link.sensitive_next:
            LOG.debug(
                "orchestrator: sensitive session — chunk dropped, will deliver via phone"
            )
            return
        if self._speaking:
            self._park_chunk(chunk, rev)
            return
        await self._inject(chunk, final, rev)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _park_chunk(self, chunk: str, rev: int) -> None:
        if len(self._stash) >= _STASH_MAX_CHUNKS:
            LOG.warning("orchestrator: stash full — dropping oldest chunk")
            self._stash.pop(0)
        self._stash.append((chunk, rev))

    async def _flush_stash(self) -> None:
        """After barge-in goes idle: replay stashed chunks if rev still matches."""
        cur_rev = self.snapshot.rev
        to_inject = [(c, r) for c, r in self._stash if r >= cur_rev]
        self._stash.clear()
        if not to_inject:
            LOG.debug("orchestrator: stash discarded (rev moved on)")
            return
        LOG.info("orchestrator: flushing %d stashed chunk(s)", len(to_inject))
        last = len(to_inject) - 1
        for i, (chunk, _r) in enumerate(to_inject):
            if self.snapshot.rev != cur_rev:
                break
            await self._inject(chunk, final=(i == last), rev=cur_rev)
            if i != last:
                await asyncio.sleep(0.05)

    async def _inject(self, chunk: str, final: bool, rev: int) -> None:  # noqa: ARG002
        text = (
            f"[here is additional context from my engine — weave it naturally "
            f"into your next spoken reply, do not read it verbatim:] {chunk}"
        )
        delivered = await self.link.inject_text_when_idle(text, sensitive=False)
        if not delivered:
            LOG.debug("orchestrator: inject lost (session closed)")

    # ── P4: self-barge guard ──────────────────────────────────────────────────

    async def on_vad_event(self, client_ws) -> None:
        """Count rapid speech_started while Nemo was speaking; demote if too many."""
        import time

        now = time.monotonic()
        self._barge_ts = [t for t in self._barge_ts if now - t < _BARGE_WINDOW_SEC]
        self._barge_ts.append(now)
        if self._speaking and len(self._barge_ts) >= _BARGE_THRESHOLD:
            self._barge_ts.clear()
            LOG.warning(
                "orchestrator: self-barge threshold reached — demoting to half-duplex"
            )
            try:
                await client_ws.send(json.dumps({"type": "fullduplex_demote"}))
            except Exception as exc:  # noqa: BLE001
                LOG.debug("orchestrator: demote send failed: %s", exc)

    def snapshot_slice(self) -> dict:
        """Return a safe capped snapshot dict for inclusion in kick payloads."""
        return self.snapshot.to_slice()

    def close(self) -> None:
        session_registry.unregister(self.session_id)
        self._stash.clear()
        if self._m2_task and not self._m2_task.done():
            self._m2_task.cancel()
        LOG.info("orchestrator: session %s ended", self.session_id[:8])


def make_orchestrator(link: QwenLink) -> Orchestrator:
    """Factory called from qwen_realtime.run_session when VOICE_ORCHESTRATOR=1."""
    import uuid

    return Orchestrator(link=link, session_id=str(uuid.uuid4()))
