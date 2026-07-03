"""Read recent phone notifications (Jarvis message awareness).

The Android app's NotificationListenerService captures notifications from
allowlisted apps (Telegram, WhatsApp by default) into an on-device buffer. This
tool asks the phone for that buffer (via action_bridge) so Nemo can give a brief
spoken rundown on explicit request.

Wire notes:
- The app returns the buffer as a JSON string in the action_result ``text``
  field. ``action_bridge.resolve`` only forwards ``ok/text/error/image_b64``, so
  a list can't ride its own key (the image_b64 precedent) — it rides ``text``.
- This tool is NOT in voice_brain.FUNCTIONS: it's invoked ONLY by the explicit
  ``is_messages_intent`` recovery in qwen_realtime, so the model can never pull
  messages on its own — explicit-request-only by construction.
- Privacy: the summary turn is marked sensitive (see _SENSITIVE_TOOLS in
  qwen_realtime) so message content is never journaled / re-uploaded / fact-
  extracted.
"""

from __future__ import annotations

import json
import logging

LOG = logging.getLogger("nemo.messages")

TOOL_NAMES = {"read_messages"}
_MAX_ITEMS = 20
_MAX_TEXT = 160


async def dispatch(name: str, args: dict, bridge) -> str:
    """Ask the phone for its captured-notification buffer; return compact JSON."""
    if name != "read_messages":
        return json.dumps({"error": f"unknown messages tool {name}"})
    if bridge is None:
        return json.dumps({"error": "phone not connected"})
    result = await bridge.run("read_messages", timeout=15.0)
    if not result.get("ok"):
        # Distinguish phone-unreachable / access-not-granted from "no messages"
        # so Nemo never says "you have no messages" when he just can't reach the
        # phone. The app sends a specific error string for the no-access case.
        return json.dumps({"error": result.get("error") or "couldn't reach your phone"})
    try:
        entries = json.loads(result.get("text") or "[]")
    except (ValueError, TypeError):
        entries = []
    if not isinstance(entries, list) or not entries:
        return json.dumps({"result": "nothing new"})
    items = [
        {
            "app": e.get("app"),
            "from": e.get("sender"),
            "text": (e.get("text") or "")[:_MAX_TEXT],
        }
        for e in entries[:_MAX_ITEMS]
        if isinstance(e, dict)
    ]
    return json.dumps({"messages": items})
