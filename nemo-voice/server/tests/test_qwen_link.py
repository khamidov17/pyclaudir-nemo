"""QwenLink: the per-session serializer for the upstream Qwen socket.

Covers the hazards the rewrite was meant to kill: interleaved response.create,
the background-task cap, cancellation on teardown, and the closed-socket guard.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from qwen_link import QwenLink


class FakeSocket:
    """Records every frame sent; lets a test pause mid-send to force a race."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._gate: asyncio.Event | None = None

    def gate_on(self) -> asyncio.Event:
        self._gate = asyncio.Event()
        return self._gate

    async def send(self, raw: str) -> None:
        if self._gate is not None and not self._gate.is_set():
            await self._gate.wait()
        self.sent.append(json.loads(raw))


@pytest.mark.asyncio
async def test_respond_to_pair_is_atomic_under_concurrency():
    """Two concurrent respond_to calls never interleave their item/response
    pair — each item.create is immediately followed by its response.create."""
    sock = FakeSocket()
    link = QwenLink(sock)
    await asyncio.gather(
        link.respond_to({"type": "function_call_output", "call_id": "a"}),
        link.respond_to({"type": "function_call_output", "call_id": "b"}),
    )
    types = [m["type"] for m in sock.sent]
    assert types == [
        "conversation.item.create",
        "response.create",
        "conversation.item.create",
        "response.create",
    ]


@pytest.mark.asyncio
async def test_background_tasks_capped_at_two():
    """At most max_bg tools run at once; a third waits for a slot."""
    sock = FakeSocket()
    link = QwenLink(sock, max_bg=2)
    running = 0
    peak = 0
    release = asyncio.Event()

    async def slow():
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1

    for _ in range(5):
        link.spawn_bg(slow)
    await asyncio.sleep(0.05)  # let the first batch grab slots
    assert peak == 2  # never more than the cap in flight
    release.set()
    await link.aclose()


@pytest.mark.asyncio
async def test_aclose_cancels_inflight_background_tasks():
    """A task still waiting on teardown is cancelled, not leaked."""
    sock = FakeSocket()
    link = QwenLink(sock)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hang():
        started.set()
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    link.spawn_bg(hang)
    await started.wait()
    await link.aclose()
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_sends_noop_after_close():
    """Once closed, send/respond_to/inject_text drop silently instead of
    writing into a socket that's tearing down."""
    sock = FakeSocket()
    link = QwenLink(sock)
    await link.aclose()
    await link.send({"type": "x"})
    await link.respond_to({"type": "y"})
    await link.inject_text("hi")
    assert sock.sent == []


@pytest.mark.asyncio
async def test_bg_task_skipped_if_closed_before_slot():
    """A queued task that only gets its slot after aclose() must not run its
    body (it would write into a closed session)."""
    sock = FakeSocket()
    link = QwenLink(sock, max_bg=1)
    gate = asyncio.Event()
    ran_second = False

    async def occupy():
        await gate.wait()

    async def second():
        nonlocal ran_second
        ran_second = True

    link.spawn_bg(occupy)  # grabs the only slot, holds it
    await asyncio.sleep(0.01)
    link.spawn_bg(second)  # queued behind occupy
    await asyncio.sleep(0.01)
    # Close while `second` is still waiting for the semaphore.
    close = asyncio.create_task(link.aclose())
    gate.set()  # free the slot → second acquires it, but session is closed
    await close
    assert ran_second is False


@pytest.mark.asyncio
async def test_wait_until_idle_blocks_while_speaking():
    """A background result holds until the current reply ends."""
    link = QwenLink(FakeSocket())
    link.mark_speaking()
    waiter = asyncio.create_task(link.wait_until_idle())
    await asyncio.sleep(0.01)
    assert not waiter.done()  # still parked while Nemo speaks
    link.mark_idle()
    await asyncio.wait_for(waiter, 0.1)  # released at the gap


@pytest.mark.asyncio
async def test_aclose_releases_idle_waiter():
    """Teardown must not leave a background task parked forever on the gate."""
    link = QwenLink(FakeSocket())
    link.mark_speaking()
    waiter = asyncio.create_task(link.wait_until_idle())
    await asyncio.sleep(0.01)
    await link.aclose()
    await asyncio.wait_for(waiter, 0.1)  # unblocked by aclose


@pytest.mark.asyncio
async def test_inject_text_shapes_a_user_turn():
    sock = FakeSocket()
    link = QwenLink(sock)
    await link.inject_text("hello there")
    item = sock.sent[0]["item"]
    assert item["role"] == "user"
    assert item["content"][0]["text"] == "hello there"
    assert sock.sent[1]["type"] == "response.create"
