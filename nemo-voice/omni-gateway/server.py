"""Omni gateway entrypoint — websocket server on ava-gpu.

Accepts the same connections qwen_pump makes to DashScope; point the voice
server at it with QWEN_REALTIME_URL=ws://<ava-gpu>:8770 (auth via the same
Bearer header, checked against OMNI_GATEWAY_TOKEN).

Run: python server.py   (needs `websockets` + `aiohttp`)
"""

from __future__ import annotations

import asyncio
import logging
import os

import websockets

from omni_backend import VllmOmniBackend
from protocol import GatewaySession

LOG = logging.getLogger("gateway.server")

PORT = int(os.environ.get("OMNI_GATEWAY_PORT", "8770"))
TOKEN = os.environ.get("OMNI_GATEWAY_TOKEN", "").strip()


def _authorized(headers) -> bool:
    if not TOKEN:
        return True  # no token configured → open (bind behind firewall!)
    auth = headers.get("Authorization", "")
    return auth == f"Bearer {TOKEN}"


async def _handle(ws) -> None:
    if not _authorized(ws.request.headers):
        await ws.close(code=4401, reason="unauthorized")
        return
    import json

    session = GatewaySession(VllmOmniBackend())
    await ws.send(json.dumps(session.hello()))
    LOG.info("session open from %s", ws.remote_address)
    try:
        async for raw in ws:
            for event in await session.handle(raw):
                await ws.send(json.dumps(event))
    except websockets.ConnectionClosed:
        pass
    finally:
        LOG.info("session closed")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    async with websockets.serve(_handle, "0.0.0.0", PORT):
        LOG.info("omni gateway listening on :%d", PORT)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
