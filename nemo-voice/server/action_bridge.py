"""Bridge voice-agent tool calls to the phone over the app websocket.

The voice agent (Deepgram/Gemini think model) calls a phone tool; we push
``{"type": "action", "id", "command"}`` to the connected Flutter app, which
executes it (open app, set alarm, Telegram message, …) and answers with
``{"type": "action_result", "id", "ok", "text"|"error"}``. The pending-future
map matches answers to calls so the agent can confirm the outcome out loud.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

LOG = logging.getLogger("nemo.action_bridge")

_TIMEOUT_SEC = 20.0
# Rate limit so a runaway/confused model can't machine-gun the phone with
# actions. Matches the engine's phone_broker limit (10 per 10s).
_MAX_ACTIONS = 10
_WINDOW_SEC = 10.0


class ActionBridge:
    """Send phone commands to the connected app and await their results."""

    def __init__(self, client_ws) -> None:
        self._ws = client_ws
        self._pending: dict[str, asyncio.Future] = {}
        self._recent: list[float] = []  # monotonic timestamps of recent actions

    def _rate_limited(self) -> bool:
        now = time.monotonic()
        self._recent = [t for t in self._recent if now - t < _WINDOW_SEC]
        if len(self._recent) >= _MAX_ACTIONS:
            return True
        self._recent.append(now)
        return False

    async def run(self, command: str, timeout: float = _TIMEOUT_SEC) -> dict:
        if self._rate_limited():
            LOG.warning("phone action rate-limited: %s", command)
            return {"ok": False, "error": "too many phone actions — slow down"}
        action_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[action_id] = fut
        LOG.info("phone action: %s (id=%s)", command, action_id)
        try:
            await self._ws.send(
                json.dumps({"type": "action", "id": action_id, "command": command})
            )
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error": f"phone did not respond within {int(timeout)}s",
            }
        except Exception as exc:  # socket closed mid-action
            return {"ok": False, "error": str(exc)}
        finally:
            self._pending.pop(action_id, None)

    def resolve(self, msg: dict) -> None:
        """Called by the client receive loop for each action_result frame."""
        fut = self._pending.get(str(msg.get("id", "")))
        if fut is None or fut.done():
            return
        fut.set_result(
            {
                "ok": bool(msg.get("ok")),
                "text": msg.get("text"),
                "error": msg.get("error"),
            }
        )
