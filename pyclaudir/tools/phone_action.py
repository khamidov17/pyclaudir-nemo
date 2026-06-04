"""phone_action — one MCP tool for all phone control.

Commands (sent as plain text, internally validated):
  status              — check if phone is connected + capabilities
  list_apps           — return installed app package names
  screenshot          — capture screen → image visible to CC
  camera              — capture camera frame → image
  ui_tree             — return accessibility tree as text
  tap X Y             — tap at pixel coordinates
  swipe X1 Y1 X2 Y2  — swipe gesture
  type TEXT           — type text at focused input
  press KEYCODE       — press button (back=back, home=home, recents=recents)
  open PACKAGE        — launch app by package name (e.g. org.telegram.messenger)
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from .base import BaseTool, ToolResult

log = logging.getLogger(__name__)

_VALID_COMMANDS = frozenset([
    "status", "list_apps", "screenshot", "camera", "ui_tree",
    "tap", "swipe", "type", "press", "open",
])

_PRESS_MAP = {"back": "back", "home": "home", "recents": "recents"}


class PhoneActionArgs(BaseModel):
    command: str = Field(
        description=(
            "Phone command. Examples: 'screenshot', 'tap 150 300', "
            "'type hello world', 'open org.telegram.messenger', "
            "'swipe 540 1200 540 400', 'press back', 'ui_tree', 'list_apps', 'status'."
        )
    )


class PhoneActionTool(BaseTool):
    name = "phone_action"
    description = (
        "Control the owner's Android phone. One tool for everything: "
        "screenshot (returns image), camera, tap, swipe, type, press, "
        "open app, ui_tree (read screen), list_apps, status. "
        "Use status first to check connection. Use screenshot to see the screen."
    )
    args_model = PhoneActionArgs

    async def run(self, args: PhoneActionArgs) -> ToolResult:
        broker = getattr(self.ctx, "phone_broker", None)
        if broker is None:
            return ToolResult(
                content="Phone control not available — NEMO_APP_TOKEN not set.",
                is_error=True,
            )

        cmd = args.command.strip()
        if not cmd:
            return ToolResult(content="empty command", is_error=True)

        # Validate command verb
        verb = cmd.split()[0].lower()
        if verb not in _VALID_COMMANDS:
            return ToolResult(
                content=f"unknown command '{verb}'. valid: {', '.join(sorted(_VALID_COMMANDS))}",
                is_error=True,
            )

        log.info("phone_action: %r", cmd[:80])
        result = await broker.send_action(cmd)

        if not result.get("ok"):
            return ToolResult(
                content=f"phone error: {result.get('error', 'unknown')}",
                is_error=True,
            )

        # Image result — return as vision block so CC can see it
        if result.get("image_path"):
            path = Path(result["image_path"])
            if path.exists():
                return ToolResult(
                    content=f"image captured: {path.name}",
                    image_path=path,
                )
            return ToolResult(content="image file missing after capture", is_error=True)

        # Text result
        text = result.get("text") or result.get("data") or "ok"
        return ToolResult(content=str(text)[:4000])
