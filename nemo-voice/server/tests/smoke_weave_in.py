#!/usr/bin/env python3
"""Local end-to-end smoke for the weave-in channel — no Qwen/Claude needed.

Stands up the REAL voice HTTP server (`voice_http`), registers a live
`Orchestrator` whose link records what gets "spoken", then fires HMAC-signed
chunks at `POST /internal/brain_result` over real TCP — exactly what the engine
does via `voice_bridge.post_chunk`. Watch:

  1. a fresh chunk (rev == delegate rev)  → woven (spoken)
  2. a stale chunk (user moved on)        → dropped
  3. a forged signature                   → 401

Run:  cd nemo-voice/server && python3 tests/smoke_weave_in.py
"""

from __future__ import annotations

import os

# Module-level flags are read at import — set them FIRST.
os.environ["VOICE_INTERNAL_TOKEN"] = "smoke-internal-token"
os.environ["VOICE_WEAVE_IN"] = "1"

import asyncio  # noqa: E402
import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402
import uuid  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_http  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402

_TOKEN = os.environ["VOICE_INTERNAL_TOKEN"].encode()


def _sign(body: bytes) -> str:
    return hmac.new(_TOKEN, body, hashlib.sha256).hexdigest()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _post(url: str, payload: dict, *, sig: str | None = None) -> int:
    body = json.dumps(payload).encode()
    headers = {"X-Internal-Sig": sig if sig is not None else _sign(body)}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, data=body, headers=headers) as r:
            return r.status


def _orch():
    link = MagicMock()
    link.sensitive_next = False
    link.inject_text_when_idle = AsyncMock(return_value=True)
    orch = Orchestrator(link=link, session_id=str(uuid.uuid4()))
    return orch, link


async def main() -> int:
    port = _free_port()
    runner = web.AppRunner(voice_http.build_app("smoke"))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    url = f"http://127.0.0.1:{port}/internal/brain_result"
    print(f"voice HTTP up on 127.0.0.1:{port}\n")

    failures = 0
    orch, link = _orch()
    # Simulate a real user turn so the snapshot has a rev, like production.
    await orch.on_final_transcript("write me code to sort a list")
    delegate_rev = orch.snapshot.rev
    print(f"session {orch.session_id[:8]}  delegate_rev={delegate_rev}\n")

    # 1. fresh chunk → should be spoken
    st = await _post(url, {"session_id": orch.session_id, "chunk": "Here is the sorted list.", "final": True, "rev": delegate_rev})
    await asyncio.sleep(0.1)  # let the FIFO consumer drain
    spoken = link.inject_text_when_idle.await_count > 0
    ok = st == 202 and spoken
    failures += not ok
    said = link.inject_text_when_idle.await_args.args[0] if spoken else "(nothing)"
    print(f"1) fresh chunk      → HTTP {st}, spoken={spoken}  {'✅' if ok else '❌'}")
    print(f"     Nemo got: {said[:90]}\n")

    # 2. stale chunk (user moved on → snapshot rev advances) → should be dropped
    await orch.on_final_transcript("never mind, what's the weather")
    link.inject_text_when_idle.reset_mock()
    st = await _post(url, {"session_id": orch.session_id, "chunk": "Here is the sorted list.", "final": True, "rev": delegate_rev})
    await asyncio.sleep(0.1)
    dropped = link.inject_text_when_idle.await_count == 0
    ok = st == 202 and dropped
    failures += not ok
    print(f"2) stale chunk      → HTTP {st}, dropped={dropped}  {'✅' if ok else '❌'}\n")

    # 3. forged signature → 401
    st = await _post(url, {"session_id": orch.session_id, "chunk": "x", "final": True, "rev": delegate_rev}, sig="deadbeef")
    ok = st == 401
    failures += not ok
    print(f"3) forged signature → HTTP {st}  {'✅' if ok else '❌'}\n")

    orch.close()
    await runner.cleanup()
    print("─" * 40)
    print("ALL GOOD ✅" if failures == 0 else f"{failures} CHECK(S) FAILED ❌")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
