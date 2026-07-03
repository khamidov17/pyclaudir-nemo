"""ActionBridge: match phone action_result frames back to pending calls.

Focus on the vision regression — resolve() must forward the captured image, in
either snake or camel form — plus the rate-limit and timeout paths.
"""

from __future__ import annotations

import asyncio

import pytest

from action_bridge import ActionBridge


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, raw: str) -> None:
        self.sent.append(raw)


async def _run_and_resolve(reply: dict) -> dict:
    """Drive one run() and feed it a matching action_result built from `reply`."""
    bridge = ActionBridge(FakeWS())
    task = asyncio.create_task(bridge.run("camera"))
    await asyncio.sleep(0)  # let run() register the pending future + send
    action_id = next(iter(bridge._pending))
    bridge.resolve({"id": action_id, **reply})
    return await task


@pytest.mark.asyncio
async def test_resolve_forwards_image_b64_snake():
    out = await _run_and_resolve({"ok": True, "image_b64": "ABC123"})
    assert out["image_b64"] == "ABC123"


@pytest.mark.asyncio
async def test_resolve_forwards_image_b64_camel():
    """The app may send camelCase imageB64 — normalize it to snake."""
    out = await _run_and_resolve({"ok": True, "imageB64": "ZZZ"})
    assert out["image_b64"] == "ZZZ"


@pytest.mark.asyncio
async def test_resolve_no_image_is_none():
    out = await _run_and_resolve({"ok": True, "text": "done"})
    assert out["image_b64"] is None
    assert out["text"] == "done"


@pytest.mark.asyncio
async def test_rate_limit_blocks_runaway():
    bridge = ActionBridge(FakeWS())
    # Exhaust the window without ever resolving (each run will time out, but the
    # rate limiter counts the attempt first).
    for _ in range(10):
        bridge._recent.append(0.0)  # pre-fill near-now is harder; use monotonic
    # Force the window full with current timestamps.
    import time

    bridge._recent = [time.monotonic()] * 10
    out = await bridge.run("open_app")
    assert out["ok"] is False
    assert "too many" in out["error"]


@pytest.mark.asyncio
async def test_timeout_returns_error_not_hang():
    bridge = ActionBridge(FakeWS())
    out = await bridge.run("camera", timeout=0.05)  # nobody resolves it
    assert out["ok"] is False
    assert "did not respond" in out["error"]
