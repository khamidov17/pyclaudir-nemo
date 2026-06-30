"""Voice service entry point: WebSocket auth + backend routing.

Extracted HTTP handlers → voice_http.py; Deepgram session → deepgram_bridge.py;
Qwen pump → qwen_pump.py. This file is now just auth, routing, and server setup.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path

import websockets
from dotenv import load_dotenv

from voice_metrics import METRICS

ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ROOT_ENV, override=False)
load_dotenv(override=False)

HOST = os.environ.get("VOICE_HOST", "0.0.0.0")
PORT = int(os.environ.get("VOICE_PORT", "3002"))
HTTP_PORT = int(os.environ.get("VOICE_HTTP_PORT", "3001"))
VOICE_BACKEND = os.environ.get("VOICE_BACKEND", "deepgram").strip().lower()

LOG = logging.getLogger("nemo.streaming_service")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def _device_allowed(device_id: str) -> bool:
    allow = {
        d.strip()
        for d in os.environ.get("NEMO_PAIRED_DEVICE_IDS", "").split(",")
        if d.strip()
    }
    return not allow or device_id in allow


def _tls_context():
    cert = os.environ.get("VOICE_TLS_CERT", "").strip()
    key = os.environ.get("VOICE_TLS_KEY", "").strip()
    if not cert or not key:
        return None
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return ctx


def _redacted_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


async def _handle_client(client_ws) -> None:
    client_id = id(client_ws)
    expected_token = _required_env("NEMO_APP_TOKEN")
    try:
        first = await asyncio.wait_for(client_ws.recv(), timeout=10)
        auth = json.loads(first)
    except Exception:
        await client_ws.close(code=4001, reason="auth timeout")
        return
    if auth.get("type") != "auth" or not hmac.compare_digest(
        auth.get("token", ""), expected_token
    ):
        LOG.warning("Voice auth rejected for client %s", client_id)
        await client_ws.send(json.dumps({"type": "error", "message": "unauthorized"}))
        await client_ws.close(code=4001, reason="unauthorized")
        return
    device_id = str(auth.get("device_id", "")).strip()
    if not _device_allowed(device_id):
        LOG.warning("Voice device rejected: %r (client %s)", device_id, client_id)
        await client_ws.send(
            json.dumps({"type": "error", "message": "device not paired"})
        )
        await client_ws.close(code=4003, reason="device not paired")
        return
    LOG.info(
        "Voice auth accepted for client %s device=%s (backend=%s)",
        client_id,
        device_id or "?",
        VOICE_BACKEND,
    )
    if VOICE_BACKEND == "qwen":
        import qwen_realtime

        await qwen_realtime.run_session(client_ws, auth.get("voice"))
        return
    if VOICE_BACKEND == "gemini":
        import gemini_streaming

        await gemini_streaming.run_session(client_ws)
        return
    import deepgram_bridge

    await deepgram_bridge.run_session(
        client_ws, auth.get("voice"), api_key=_required_env("DEEPGRAM_API_KEY")
    )


async def _ws_handler(websocket) -> None:
    METRICS.session_start()
    try:
        await _handle_client(websocket)
    except websockets.exceptions.ConnectionClosed:
        LOG.info("Voice connection closed")
    except Exception as exc:
        LOG.exception("Voice connection failed: %s", exc)
        try:
            await websocket.send(json.dumps({"type": "error", "message": str(exc)}))
        except Exception:  # noqa: BLE001
            pass
    finally:
        METRICS.session_end()


def _check_required_env() -> None:
    required = ["NEMO_APP_TOKEN", "VOICE_INTERNAL_TOKEN"]
    backend_key = {"qwen": "DASHSCOPE_API_KEY", "gemini": "GEMINI_API_KEY"}.get(
        VOICE_BACKEND, "DEEPGRAM_API_KEY"
    )
    required.append(backend_key)
    missing = [n for n in required if not os.environ.get(n, "").strip()]
    if missing:
        raise SystemExit(
            f"FATAL: missing required env: {', '.join(missing)}. "
            f"Set them in the .env next to this service before starting."
        )


async def main() -> None:
    _check_required_env()
    ssl_ctx = _tls_context()
    scheme = "wss" if ssl_ctx else "ws"
    LOG.info(
        "Starting Nemo voice bridge (backend=%s) on %s://%s:%d",
        VOICE_BACKEND,
        scheme,
        HOST,
        PORT,
    )
    from voice_http import serve

    async with websockets.serve(_ws_handler, HOST, PORT, ssl=ssl_ctx):
        await asyncio.gather(serve(HTTP_PORT, ssl_ctx, VOICE_BACKEND), asyncio.Future())


if __name__ == "__main__":
    asyncio.run(main())
