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

import session_registry
from qwen_link import QwenLink
from semantic_router import RouteDecision, route_tier
from state_snapshot import StateSnapshot

LOG = logging.getLogger("nemo.orchestrator")

_SHARED_CTX = os.environ.get("VOICE_SHARED_CONTEXT", "0").strip() == "1"
_WEAVE_IN = os.environ.get("VOICE_WEAVE_IN", "0").strip() == "1"

# Self-barge detector (P4)
_BARGE_THRESHOLD = int(os.environ.get("BARGE_IN_SELF_THRESHOLD", "3"))
_BARGE_WINDOW_SEC = 10.0

# Max chars of stashed chunk text before we give up and discard.
_STASH_MAX_CHARS = 6_000


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
        # Single-consumer FIFO queue so the clauses of one answer are woven in
        # the order the engine produced them — a `create_task` per POST does NOT
        # preserve order and would scramble a multi-clause answer.
        self._chunk_q: asyncio.Queue[tuple[str, bool, int]] | None = None
        self._consumer: asyncio.Task | None = None
        session_registry.register(session_id, self)
        LOG.info("orchestrator: session %s started", session_id[:8])

    def enqueue_chunk(self, chunk: str, final: bool, rev: int) -> None:
        """Called by the HTTP handler. Order-preserving: a single consumer task
        drains the queue and injects chunks one at a time."""
        if self._chunk_q is None:
            self._chunk_q = asyncio.Queue()
            self._consumer = asyncio.create_task(self._consume())
        self._chunk_q.put_nowait((chunk, final, rev))

    async def _consume(self) -> None:
        assert self._chunk_q is not None
        while True:
            chunk, final, rev = await self._chunk_q.get()
            try:
                await self.on_background_chunk(chunk, final, rev)
            except Exception as exc:  # noqa: BLE001 — never kill the consumer
                LOG.warning("orchestrator: chunk handling failed: %s", exc)

    # ── pump callbacks ────────────────────────────────────────────────────────

    async def on_final_transcript(self, text: str) -> RouteDecision:
        """Called when the user's utterance is finalized (VAD done)."""
        self.snapshot.set_transcript(text)
        decision = route_tier(text)
        if _SHARED_CTX:
            self.snapshot.set_route(decision.value)
        LOG.debug("orchestrator: route=%s for %r", decision.value, text[:60])
        return decision

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
        total = sum(len(c) for c, _ in self._stash)
        if total >= _STASH_MAX_CHARS:
            LOG.warning("orchestrator: stash full — dropping chunk")
            return
        self._stash.append((chunk, rev))

    async def _flush_stash(self) -> None:
        """After barge-in goes idle: replay stashed chunks if rev still matches."""
        cur_rev = self.snapshot.rev
        to_inject = [(c, r) for c, r in self._stash if r >= cur_rev]
        self._stash.clear()
        if not to_inject:
            LOG.debug("orchestrator: stash discarded (rev moved on)")
            return
        joined = " ".join(c for c, _ in to_inject)
        LOG.info("orchestrator: flushing %d stashed chunk(s)", len(to_inject))
        bridge_text = (
            f"[continuing the answer you just started — pick up naturally:] {joined}"
        )
        await self._inject(bridge_text, True, cur_rev)

    async def _inject(self, chunk: str, final: bool, rev: int) -> None:  # noqa: ARG002
        # This is the engine's actual ANSWER (or part of it) — Nemo should relay
        # it, not treat it as vague context. Keep it natural / in his own words.
        text = (
            "[Your engine brain just produced this part of the answer — relay it "
            f"to Avazbek now, naturally and in your own words:] {chunk}"
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
        if self._consumer is not None:
            self._consumer.cancel()
            self._consumer = None
        self._stash.clear()
        # Drop this session's rate-limit bucket so it doesn't leak.
        try:
            import voice_http

            voice_http.forget_session(self.session_id)
        except Exception:  # noqa: BLE001
            pass
        LOG.info("orchestrator: session %s ended", self.session_id[:8])


def make_orchestrator(link: QwenLink) -> Orchestrator:
    """Factory called from qwen_realtime.run_session when VOICE_ORCHESTRATOR=1."""
    import uuid

    return Orchestrator(link=link, session_id=str(uuid.uuid4()))
