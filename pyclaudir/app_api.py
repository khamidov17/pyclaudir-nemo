"""Mobile app WebSocket bridge.

Accepts connections from the nemo-app Flutter client. Handles:
- Chat messages (phone → engine)
- Nemo replies (engine → phone via send_message broadcast)
- Phone action commands (engine/MCP → phone) and results (phone → broker)

Exactly one device may hold the control lease at a time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from pathlib import Path
from fastapi.responses import FileResponse
from .models import ChatMessage
from .phone_broker import PhoneBroker
from .tools.base import ToolContext

log = logging.getLogger("pyclaudir.app_api")

_MSG_COUNTER = 0


def _next_msg_id() -> int:
    global _MSG_COUNTER
    _MSG_COUNTER -= 1
    return _MSG_COUNTER


class AppApiServer:
    """WebSocket server bridging Flutter app ↔ pyclaudir engine + phone broker."""

    def __init__(
        self,
        token: str,
        owner_id: int,
        ctx: ToolContext,
        broker: PhoneBroker,
        data_dir: "Path | None" = None,
    ) -> None:
        self._token = token
        self._owner_id = owner_id
        self._ctx = ctx
        self._broker = broker
        self._data_dir = data_dir
        self._engine: object | None = None
        self._server: uvicorn.Server | None = None
        self.app = self._build_app()

    def set_engine(self, engine: object) -> None:
        self._engine = engine

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Nemo App API", docs_url=None, redoc_url=None)
        # The native app sends no Origin header and needs no CORS. A browser is
        # not a supported client, so don't hand one a wildcard grant to a
        # phone-control API — restrict to explicit origins if ever needed.
        _origins = [
            o.strip()
            for o in os.environ.get("NEMO_CORS_ORIGINS", "").split(",")
            if o.strip()
        ]
        if _origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=_origins,
                allow_methods=["*"],
                allow_headers=["*"],
            )

        def _check_token(request_token: str) -> bool:
            import hmac

            return hmac.compare_digest(request_token, self._token)

        def _auth(request: Request, token: str = "") -> bool:
            """Accept token from Authorization header or query param."""
            bearer = request.headers.get("authorization", "")
            if bearer.lower().startswith("bearer "):
                return _check_token(bearer[7:].strip())
            return _check_token(token)

        @app.get("/apk/version")
        async def apk_version(request: Request, token: str = "") -> dict:
            """Authenticated version check."""
            if not _auth(request, token):
                from fastapi import HTTPException

                raise HTTPException(401, "unauthorized")
            try:
                v = (
                    int((self._data_dir / "apk" / "version.txt").read_text().strip())
                    if self._data_dir
                    else 0
                )
            except Exception:
                v = 0
            # Include APK hash so client can verify before installing
            apk_hash = ""
            if self._data_dir:
                import hashlib

                apk_path = self._data_dir / "apk" / "nemo-latest.apk"
                if apk_path.exists():
                    apk_hash = hashlib.sha256(apk_path.read_bytes()).hexdigest()
            return {"version": v, "sha256": apk_hash}

        @app.get("/apk/download")
        async def apk_download(request: Request, token: str = "") -> FileResponse:
            """Authenticated APK download."""
            if not _auth(request, token):
                from fastapi import HTTPException

                raise HTTPException(401, "unauthorized")
            if self._data_dir is None:
                from fastapi import HTTPException

                raise HTTPException(404, "APK not available")
            apk_path = self._data_dir / "apk" / "nemo-latest.apk"
            if not apk_path.exists():
                from fastapi import HTTPException

                raise HTTPException(404, "APK not found — run scripts/build_apk.sh")
            return FileResponse(
                str(apk_path),
                media_type="application/vnd.android.package-archive",
                filename="Nemo.apk",
            )

        @app.get("/health")
        async def health(request: Request, token: str = "") -> dict:
            """Status — requires auth."""
            if not _auth(request, token):
                from fastapi import HTTPException

                raise HTTPException(401, "unauthorized")
            return {
                "status": "ok",
                "clients": len(self._ctx.app_clients),
                "phone_connected": self._broker.connected,
            }

        @app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket, device_id: str = "") -> None:
            # Accept first, then authenticate via first message (token not in URL)
            await websocket.accept()
            try:
                first = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
            except Exception:
                await websocket.close(code=4001, reason="auth timeout")
                return
            if first.get("type") != "auth" or not _check_token(first.get("token", "")):
                await websocket.close(code=4001, reason="unauthorized")
                log.warning("rejected connection (bad token in first message)")
                return
            device_id = first.get("device_id") or device_id or f"device-{id(websocket)}"

            self._ctx.app_clients.add(websocket)
            did = device_id
            log.info(
                "app connected device=%s (%d total)", did, len(self._ctx.app_clients)
            )

            await websocket.send_text(
                f'{{"type":"connected","status":"ok","device_id":"{did}"}}'
            )
            try:
                await self._broker.register_device(did, websocket, [])
            except PermissionError:
                await websocket.close(code=4003, reason="device not paired")
                self._ctx.app_clients.discard(websocket)
                return

            try:
                while True:
                    data = await websocket.receive_json()
                    await self._dispatch(data, did, websocket)
            except WebSocketDisconnect:
                pass
            except Exception as exc:
                log.warning("app client error device=%s: %s", did, exc)
            finally:
                self._ctx.app_clients.discard(websocket)
                await self._broker.unregister_device(did)
                log.info(
                    "app disconnected device=%s (%d remaining)",
                    did,
                    len(self._ctx.app_clients),
                )

        return app

    async def _dispatch(self, data: dict, device_id: str, websocket: WebSocket) -> None:
        msg_type = data.get("type", "message")

        if msg_type == "message":
            text = (data.get("text") or "").strip()
            if self._engine is None:
                return

            # Handle media attachment (image/PDF sent as base64)
            media = data.get("media")
            if media and isinstance(media, dict):
                import base64 as _b64
                import uuid as _uuid

                raw = _b64.b64decode(media.get("data", ""))
                mime = media.get("mime", "application/octet-stream")
                ext = "jpg" if "image" in mime else "pdf" if "pdf" in mime else "bin"
                fname = f"app_media_{_uuid.uuid4().hex[:8]}.{ext}"
                att_dir = (
                    self._data_dir / "attachments" / "app" if self._data_dir else None
                )
                if att_dir:
                    att_dir.mkdir(parents=True, exist_ok=True)
                    att_path = att_dir / fname
                    att_path.write_bytes(raw)
                    text = f"{text}\n[attachment: {att_path} type={mime} size={len(raw) // 1024}KB filename={fname}]"

            if not text:
                return
            cm = ChatMessage(
                chat_id=self._owner_id,
                message_id=_next_msg_id(),
                user_id=self._owner_id,
                username="owner",
                first_name="Avazbek",
                direction="in",
                timestamp=datetime.now(timezone.utc),
                text=text,
                received_at_monotonic=time.monotonic(),
                source="app",
            )
            log.info("app→engine device=%s: %r", device_id, text[:80])
            await self._engine.submit(cm)  # type: ignore[union-attr]

        elif msg_type == "action_result":
            # Phone completed an action — deliver to waiting MCP tool call
            action_id = data.get("id", "")
            if action_id:
                # Strip image data from logs — never audit-log base64
                safe = {k: v for k, v in data.items() if k != "image_b64"}
                log.debug(
                    "action_result device=%s id=%s ok=%s %s",
                    device_id,
                    action_id,
                    data.get("ok"),
                    safe,
                )
                self._broker.deliver_result(action_id, data)

        elif msg_type == "panic":
            # Emergency stop — kill phone control session immediately
            log.warning(
                "PANIC received from device=%s — stopping phone control", device_id
            )
            await self._broker.unregister_device(device_id)
            await websocket.send_text('{"type":"panic_ack","status":"stopped"}')
            return  # Close connection

        elif msg_type == "register":
            # Update capabilities only — never replace the websocket with None
            caps = data.get("capabilities", [])
            await self._broker.update_capabilities(device_id, caps)

        else:
            log.debug("unknown message type=%s from device=%s", msg_type, device_id)

    async def start(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        import os

        # TLS: set NEMO_TLS_CERT/NEMO_TLS_KEY (scripts/gen_server_cert.sh) and
        # the app connects with wss:// + cert pinning instead of cleartext.
        cert = os.environ.get("NEMO_TLS_CERT", "").strip() or None
        key = os.environ.get("NEMO_TLS_KEY", "").strip() or None
        config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            ssl_certfile=cert,
            ssl_keyfile=key,
        )
        self._server = uvicorn.Server(config)
        asyncio.create_task(self._server.serve(), name="nemo-app-api")
        scheme = "wss" if cert else "ws"
        log.info("app api %s://%s:%d/ws (phone_broker attached)", scheme, host, port)

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
