"""Per-session serialization for the upstream Qwen realtime socket.

One Qwen session is driven by two coroutines (client→Qwen and Qwen→client) plus
background tool tasks that inject their results. They all write to the SAME
upstream socket. Two hazards follow without coordination:

* Two ``response.create`` calls interleave — Qwen is already producing a
  response when a background task asks for another, so the second is dropped and
  that background answer is silently lost. The fix is to make each
  ``item.create`` + ``response.create`` pair atomic under one lock.
* A background task spawned in one session keeps running after the session ends
  and writes into a closed socket. The fix is a per-session task registry that is
  cancelled on teardown — not the module-global set it used to be.

``QwenLink`` wraps the socket with exactly that: one send lock, one bounded
background-task registry, scoped to a single session.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

LOG = logging.getLogger("nemo.qwen_link")


class QwenLink:
    """Owns one Qwen realtime socket for the lifetime of a single session."""

    def __init__(self, qwen, *, max_bg: int = 2) -> None:
        self._qwen = qwen
        self._send_lock = asyncio.Lock()
        self._bg_sem = asyncio.Semaphore(max_bg)
        self._bg_tasks: set[asyncio.Task] = set()
        self._closed = False
        # Set when Nemo is NOT mid-reply. A finished background tool waits for
        # this before speaking its result, so the answer lands at the next
        # natural gap instead of cutting into the current sentence (what made
        # background search feel like Nemo "randomly starts talking").
        self._idle = asyncio.Event()
        self._idle.set()
        # Set before injecting a privacy-sensitive tool result (read_messages):
        # the NEXT spoken reply (the message summary) must NOT be journaled,
        # re-seeded into a future session, or fact-extracted. The pump reads +
        # clears this in _complete_turn.
        self.sensitive_next = False

    def mark_speaking(self) -> None:
        self._idle.clear()

    def mark_idle(self) -> None:
        self._idle.set()

    async def wait_until_idle(self) -> None:
        await self._idle.wait()

    @property
    def closed(self) -> bool:
        """True once the session ended — sends no-op, so callers can fall back."""
        return self._closed

    def __aiter__(self):
        """Receiving is single-consumer (only the pump reads), so delegate
        straight to the socket — no lock needed on the read side."""
        return self._qwen.__aiter__()

    async def send(self, msg: dict) -> None:
        """Send one frame upstream, serialized against every other sender."""
        if self._closed:
            return
        async with self._send_lock:
            await self._qwen.send(json.dumps(msg))

    async def respond_to(self, item: dict) -> None:
        """Append one conversation item then request a response — atomically, so
        a concurrent inject can't slip a second response.create between the pair
        (which Qwen would drop)."""
        if self._closed:
            return
        async with self._send_lock:
            await self._qwen.send(
                json.dumps({"type": "conversation.item.create", "item": item})
            )
            await self._qwen.send(json.dumps({"type": "response.create"}))

    async def inject_text(self, text: str) -> None:
        """Inject a user-role text turn and let Nemo respond to it."""
        await self.respond_to(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )

    def spawn_bg(self, make_coro: Callable[[], Awaitable[None]]) -> None:
        """Run a slow tool off the conversation, bounded to max_bg at once and
        owned by this session so it can't outlive it."""
        task = asyncio.create_task(self._guard(make_coro))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _guard(self, make_coro: Callable[[], Awaitable[None]]) -> None:
        async with self._bg_sem:
            if self._closed:  # session ended while we waited for a slot
                return
            try:
                await make_coro()
            except Exception as exc:  # noqa: BLE001 — never crash teardown
                LOG.warning("background tool failed: %s", exc)

    async def aclose(self) -> None:
        """Mark closed and cancel any in-flight background tasks."""
        self._closed = True
        self._idle.set()  # release any bg task parked on wait_until_idle
        for task in list(self._bg_tasks):
            task.cancel()
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)
