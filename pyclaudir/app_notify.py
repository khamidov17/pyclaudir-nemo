"""Transport-neutral owner notification — reach the phone app / desktop client.

When Nemo runs app-only (no Telegram bot), system notices (crash, stale
session, give-up) and any "tell the owner" path reach the user by broadcasting
over the app WebSocket instead of Telegram. Mirrors the broadcast loop in
``tools/send_message.py`` so both stay consistent.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("pyclaudir.app_notify")


async def broadcast_to_app(app_clients: set, text: str, chat_id: int) -> int:
    """Send a Nemo text message to every connected app/desktop client.

    Returns the number of clients reached; prunes dead sockets in place. The
    app renders ``{"type":"message"}`` frames as a Nemo reply (same shape
    send_message broadcasts), so a desktop client added later needs no change.
    """
    if not app_clients or not text:
        return 0
    payload = json.dumps({"type": "message", "text": text, "chat_id": chat_id})
    dead: set = set()
    reached = 0
    for ws in list(app_clients):
        try:
            await ws.send_text(payload)
            reached += 1
        except Exception:
            dead.add(ws)
    app_clients -= dead
    return reached
