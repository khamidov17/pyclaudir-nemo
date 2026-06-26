"""voice_bridge — send engine clause chunks to the voice server.

Outbound-only from pyclaudir. Auth: HMAC-SHA256 of the raw JSON body with
VOICE_INTERNAL_TOKEN in the X-Internal-Sig header. Fire-and-forget with a
single retry; never raises. The voice server ignores POST if the session is
gone (404) — that's the normal lifecycle, not an error.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os

import aiohttp

LOG = logging.getLogger("pyclaudir.voice_bridge")

_VOICE_URL = os.environ.get("VOICE_SERVER_URL", "http://localhost:3001")
_INTERNAL_TOKEN = os.environ.get("VOICE_INTERNAL_TOKEN", "").strip()
_ENDPOINT = f"{_VOICE_URL.rstrip('/')}/internal/brain_result"
_TIMEOUT = aiohttp.ClientTimeout(total=2.0)
_MAX_RETRIES = 1


def _sign(body: bytes) -> str:
    return hmac.new(_INTERNAL_TOKEN.encode(), body, hashlib.sha256).hexdigest()


async def post_chunk(session_id: str, chunk: str, final: bool, rev: int) -> None:
    """POST one clause chunk to the voice server. Never raises."""
    if not _INTERNAL_TOKEN:
        LOG.debug("voice_bridge: VOICE_INTERNAL_TOKEN not set — chunk not sent")
        return
    payload: dict = {
        "session_id": session_id,
        "chunk": chunk,
        "final": final,
        "rev": rev,
    }
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)
    headers = {"Content-Type": "application/json", "X-Internal-Sig": sig}
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.post(_ENDPOINT, data=body, headers=headers) as resp:
                    if resp.status in (202, 404):
                        return
                    LOG.warning(
                        "voice_bridge: unexpected status %d (attempt %d)",
                        resp.status,
                        attempt,
                    )
        except asyncio.TimeoutError:
            LOG.debug("voice_bridge: timeout on attempt %d", attempt)
        except Exception as exc:  # noqa: BLE001
            LOG.debug("voice_bridge: error on attempt %d: %s", attempt, exc)
