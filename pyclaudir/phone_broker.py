"""Phone action broker — request/response correlation over WebSocket.

One broker per server instance. The MCP tool calls broker.send_action()
which blocks (asyncio) until the phone responds or the timeout fires.
Exactly one phone device may hold the control lease at a time.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("pyclaudir.phone_broker")

# Per-command timeouts in seconds
_TIMEOUTS: dict[str, float] = {
    "screenshot": 15.0,
    "camera": 20.0,
    "ui_tree": 10.0,
    "tap": 5.0,
    "swipe": 5.0,
    "type": 8.0,
    "press": 5.0,
    "open": 10.0,
    "status": 5.0,
    "list_apps": 10.0,
}
_DEFAULT_TIMEOUT = 10.0

# Max bytes for an image result (5 MB)
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Rate: max N actions per window
_RATE_WINDOW = 10.0
_RATE_MAX = 10


@dataclass
class DeviceSession:
    device_id: str
    websocket: Any  # WebSocket
    capabilities: list[str] = field(default_factory=list)
    last_seen: float = field(default_factory=lambda: 0.0)


class PhoneBroker:
    """Correlates phone_action requests with phone WebSocket responses."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._device: DeviceSession | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._action_times: list[float] = []
        # Persistent allowlist of paired device IDs (survives restarts)
        self._allowlist_path = data_dir / "paired_devices.txt"
        self._allowlist: set[str] = self._load_allowlist()

    def _load_allowlist(self) -> set[str]:
        try:
            return set(self._allowlist_path.read_text().splitlines())
        except FileNotFoundError:
            return set()

    def _save_allowlist(self) -> None:
        self._allowlist_path.write_text("\n".join(sorted(self._allowlist)))

    def pair_device(self, device_id: str) -> None:
        """Add device_id to the persistent allowlist."""
        self._allowlist.add(device_id)
        self._save_allowlist()
        log.info("device paired: %s", device_id)

    def unpair_device(self, device_id: str) -> None:
        self._allowlist.discard(device_id)
        self._save_allowlist()
        log.info("device unpaired: %s", device_id)

    def is_paired(self, device_id: str) -> bool:
        """Check if device_id is in the persistent allowlist.

        First device auto-pairs ONLY when ``NEMO_ALLOW_AUTOPAIR`` is set
        (local dev convenience). On the VPS, leave it unset and pair
        explicitly via pair_device() — hostname-based "is this public"
        detection is unreliable behind NAT, so we never infer it.
        """
        import os

        autopair = os.environ.get("NEMO_ALLOW_AUTOPAIR", "").lower() in {
            "1",
            "true",
            "yes",
        }
        if not self._allowlist and autopair:
            self.pair_device(device_id)
            return True
        # "*" in allowlist = accept any device (owner can lock down later)
        return "*" in self._allowlist or device_id in self._allowlist

    # ------------------------------------------------------------------
    # Device registration (called by app_api)
    # ------------------------------------------------------------------

    async def register_device(
        self, device_id: str, ws: Any, capabilities: list[str]
    ) -> None:
        if not self.is_paired(device_id):
            log.warning("device %s rejected — not in allowlist", device_id)
            raise PermissionError(f"device {device_id} not paired")
        async with self._lock:
            if self._device is not None and self._device.device_id != device_id:
                old = self._device
                log.info("replacing device %s with %s", old.device_id, device_id)
                for fut in list(self._pending.values()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("device reconnected"))
                self._pending.clear()
            self._device = DeviceSession(
                device_id=device_id, websocket=ws, capabilities=capabilities
            )
            log.info("device registered: %s caps=%s", device_id, capabilities)

    async def update_capabilities(
        self, device_id: str, capabilities: list[str]
    ) -> None:
        """Update capabilities for an existing device — never nulls the websocket."""
        async with self._lock:
            if self._device and self._device.device_id == device_id:
                self._device.capabilities = capabilities
                log.info("device %s capabilities updated: %s", device_id, capabilities)

    async def unregister_device(self, device_id: str) -> None:
        async with self._lock:
            if self._device and self._device.device_id == device_id:
                self._device = None
                for fut in list(self._pending.values()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("device disconnected"))
                self._pending.clear()
                log.info("device unregistered: %s", device_id)

    # ------------------------------------------------------------------
    # Result delivery (called by app_api when phone sends action_result)
    # ------------------------------------------------------------------

    def deliver_result(self, action_id: str, result: dict) -> None:
        fut = self._pending.pop(action_id, None)
        if fut and not fut.done():
            fut.set_result(result)
        else:
            log.debug("no pending action for id=%s", action_id)

    # ------------------------------------------------------------------
    # Action dispatch (called by phone_action MCP tool)
    # ------------------------------------------------------------------

    async def send_action(self, command: str) -> dict:
        """Send command to phone, block until result or timeout."""
        if self._device is None:
            return {"ok": False, "error": "no phone connected"}

        # Rate limiting
        loop = asyncio.get_running_loop()
        now = loop.time()
        self._action_times = [t for t in self._action_times if now - t < _RATE_WINDOW]
        if len(self._action_times) >= _RATE_MAX:
            return {
                "ok": False,
                "error": f"rate limit: max {_RATE_MAX} actions per {_RATE_WINDOW}s",
            }
        self._action_times.append(now)

        action_id = str(uuid.uuid4())[:8]
        timeout = _TIMEOUTS.get(command.split()[0] if command else "", _DEFAULT_TIMEOUT)

        fut: asyncio.Future = loop.create_future()
        self._pending[action_id] = fut

        import json

        payload = json.dumps({"type": "action", "id": action_id, "command": command})
        try:
            await self._device.websocket.send_text(payload)
        except Exception as exc:
            self._pending.pop(action_id, None)
            return {"ok": False, "error": f"send failed: {exc}"}

        try:
            result = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(action_id, None)
            return {"ok": False, "error": f"timeout after {timeout}s"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        # Save image to file if present (never return raw b64 through MCP)
        if result.get("ok") and result.get("image_b64"):
            path = await self._save_image(result["image_b64"], action_id)
            result = {"ok": True, "image_path": str(path)}

        return result

    async def _save_image(self, b64: str, action_id: str) -> Path:
        raw = base64.b64decode(b64)
        if len(raw) > _MAX_IMAGE_BYTES:
            raw = raw[:_MAX_IMAGE_BYTES]
            log.warning("image truncated to %d bytes", _MAX_IMAGE_BYTES)
        path = self._data_dir / "renders" / f"phone_{action_id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    @property
    def connected(self) -> bool:
        return self._device is not None

    @property
    def device_id(self) -> str | None:
        return self._device.device_id if self._device else None
