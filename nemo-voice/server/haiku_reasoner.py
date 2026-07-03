"""haiku_reasoner — async Haiku calls via Anthropic API over aiohttp.

No anthropic SDK — voice server is a separate process; use aiohttp (already a dep).
VOICE_THINKER=0 disables all calls (returns None). Safe for fire-and-forget.
"""

from __future__ import annotations

import asyncio
import logging
import os

import aiohttp

LOG = logging.getLogger("nemo.haiku_reasoner")

_MODEL = "claude-haiku-4-5-20251001"
_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
_ENABLED = os.environ.get("VOICE_THINKER", "0").strip() == "1"
_ENDPOINT = "https://api.anthropic.com/v1/messages"


async def call(
    prompt: str, *, max_tokens: int = 120, timeout: float = 2.0
) -> str | None:
    """Single-turn Haiku call. Returns None on any error (fire-and-forget safe)."""
    if not _ENABLED or not _API_KEY:
        return None
    payload = {
        "model": _MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        async with asyncio.timeout(timeout):
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _ENDPOINT,
                    json=payload,
                    headers={
                        "x-api-key": _API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status != 200:
                        LOG.warning("haiku_reasoner: status %d", resp.status)
                        return None
                    data = await resp.json()
                    content = data.get("content", [])
                    return content[0].get("text") if content else None
    except (TimeoutError, asyncio.TimeoutError):
        LOG.debug("haiku_reasoner: timeout")
        return None
    except Exception as exc:  # noqa: BLE001 — fire-and-forget; never crash the turn
        LOG.debug("haiku_reasoner: error: %s", exc)
        return None
