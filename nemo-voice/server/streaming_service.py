from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
import websockets


ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ROOT_ENV, override=False)
load_dotenv(override=False)

HOST = os.environ.get("VOICE_HOST", "0.0.0.0")
PORT = int(os.environ.get("VOICE_PORT", "3002"))
HTTP_PORT = int(os.environ.get("VOICE_HTTP_PORT", "3001"))
DEEPGRAM_URL = "wss://agent.deepgram.com/v1/agent/converse"

LOG = logging.getLogger("nemo.deepgram_voice")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


SYSTEM_PROMPT = """You are Nemo, Avazbek's private voice assistant.

Speak naturally, warmly, and briefly. Keep replies useful in a live phone
conversation. If the user asks you to operate private tools that are not
available in this voice session, say you can help through Nemo text chat.
"""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def _redacted_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _deepgram_settings() -> dict:
    settings = {
        "type": "Settings",
        "tags": ["nemo", "android"],
        "mip_opt_out": True,
        "audio": {
            "input": {
                "encoding": "linear16",
                "sample_rate": 16000,
            },
            "output": {
                "encoding": "linear16",
                "sample_rate": 24000,
                "container": "none",
            },
        },
        "agent": {
            "listen": {
                "provider": {
                    "type": "deepgram",
                    "model": os.environ.get("DEEPGRAM_LISTEN_MODEL", "flux-general-en"),
                    "version": os.environ.get("DEEPGRAM_LISTEN_VERSION", "v2"),
                    "eot_threshold": float(os.environ.get("DEEPGRAM_EOT_THRESHOLD", "0.75")),
                    "eager_eot_threshold": float(
                        os.environ.get("DEEPGRAM_EAGER_EOT_THRESHOLD", "0.45")
                    ),
                },
            },
            "think": {
                "provider": {
                    "type": os.environ.get("DEEPGRAM_THINK_PROVIDER", "open_ai"),
                    "model": os.environ.get("DEEPGRAM_THINK_MODEL", "gpt-4o-mini"),
                    "temperature": float(os.environ.get("DEEPGRAM_TEMPERATURE", "0.7")),
                },
                "prompt": os.environ.get("DEEPGRAM_VOICE_PROMPT", SYSTEM_PROMPT),
            },
            "speak": {
                "provider": {
                    "type": "deepgram",
                    "model": os.environ.get("DEEPGRAM_SPEAK_MODEL", "aura-2-thalia-en"),
                },
            },
        },
    }
    # Greeting is OFF by default: a spoken "...Nemo..." greeting is picked up
    # by the on-device wake word and re-triggers the voice session. Set
    # DEEPGRAM_GREETING to a non-empty string to re-enable.
    greeting = os.environ.get("DEEPGRAM_GREETING", "").strip()
    if greeting:
        settings["agent"]["greeting"] = greeting
    return settings


async def _recv_deepgram(deepgram_ws, client_ws):
    async for message in deepgram_ws:
        if isinstance(message, bytes):
            await client_ws.send(json.dumps({
                "type": "audio",
                "data": base64.b64encode(message).decode("ascii"),
            }))
            continue

        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            LOG.warning("Deepgram sent non-JSON text: %r", message[:80])
            continue

        event_type = event.get("type")
        if event_type == "SettingsApplied":
            LOG.info("Deepgram settings applied")
        elif event_type == "ConversationText":
            text = event.get("content") or event.get("text") or ""
            role = event.get("role")
            LOG.info("Deepgram ConversationText role=%s: %r", role, text[:80])
            if text:
                await client_ws.send(json.dumps({
                    "type": "user_transcript" if role == "user" else "text",
                    "data": text,
                }))
        elif event_type == "AgentAudioDone":
            await client_ws.send(json.dumps({"type": "turn_complete"}))
        elif event_type == "UserStartedSpeaking":
            await client_ws.send(json.dumps({"type": "interrupted", "data": "barge_in"}))
        elif event_type == "Error":
            LOG.error("Deepgram error: %s", event)
            await client_ws.send(json.dumps({
                "type": "error",
                "message": event.get("description") or event.get("message") or "Deepgram error",
            }))
        elif event_type == "Warning":
            LOG.warning("Deepgram warning: %s", event)
        elif event_type in {"Welcome", "AgentThinking", "AgentStartedSpeaking", "History"}:
            LOG.debug("Deepgram event: %s", event_type)
        else:
            LOG.debug("Deepgram event payload: %s", event)


async def _wait_for_settings_applied(deepgram_ws):
    while True:
        message = await asyncio.wait_for(deepgram_ws.recv(), timeout=10)
        if isinstance(message, bytes):
            LOG.debug("Ignoring early binary audio before SettingsApplied")
            continue
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            LOG.warning("Deepgram sent non-JSON setup text: %r", message[:80])
            continue
        event_type = event.get("type")
        if event_type == "SettingsApplied":
            LOG.info("Deepgram settings applied")
            return
        if event_type == "Error":
            raise RuntimeError(event.get("description") or event.get("message") or str(event))
        LOG.debug("Deepgram setup event: %s", event_type)


async def _recv_client(client_ws, deepgram_ws):
    total_audio = 0
    last_log = 0
    async for raw in client_ws:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            LOG.warning("Bad JSON from APK")
            continue

        msg_type = data.get("type")
        if msg_type == "audio":
            audio = base64.b64decode(data.get("data", ""))
            if audio:
                total_audio += len(audio)
                # Log cumulative mic audio every ~8KB (~0.25s @16kHz) so we can
                # confirm the app is actually streaming the microphone.
                if total_audio - last_log >= 8000:
                    last_log = total_audio
                    LOG.info("mic audio from APK: %d bytes total", total_audio)
                await deepgram_ws.send(audio)
        elif msg_type == "end":
            await deepgram_ws.send(json.dumps({"type": "KeepAlive"}))
        elif msg_type == "auth":
            continue
        elif msg_type == "inject":
            text = data.get("text", "")
            if text:
                await deepgram_ws.send(json.dumps({
                    "type": "InjectUserMessage",
                    "content": text,
                }))


async def _send_deepgram_keepalive(deepgram_ws):
    while True:
        await asyncio.sleep(5)
        await deepgram_ws.send(json.dumps({"type": "KeepAlive"}))


async def _handle_client(client_ws):
    client_id = id(client_ws)
    expected_token = _required_env("NEMO_APP_TOKEN")
    deepgram_key = _required_env("DEEPGRAM_API_KEY")

    try:
        first = await asyncio.wait_for(client_ws.recv(), timeout=10)
        auth = json.loads(first)
    except Exception:
        await client_ws.close(code=4001, reason="auth timeout")
        return

    if auth.get("type") != "auth" or not hmac.compare_digest(
        auth.get("token", ""),
        expected_token,
    ):
        LOG.warning("Voice auth rejected for client %s", client_id)
        await client_ws.send(json.dumps({"type": "error", "message": "unauthorized"}))
        await client_ws.close(code=4001, reason="unauthorized")
        return

    LOG.info(
        "Voice auth accepted for client %s; Deepgram key hash=%s",
        client_id,
        _redacted_hash(deepgram_key),
    )

    headers = {"Authorization": f"Token {deepgram_key}"}
    async with websockets.connect(DEEPGRAM_URL, additional_headers=headers) as deepgram_ws:
        welcome = await asyncio.wait_for(deepgram_ws.recv(), timeout=8)
        LOG.info("Deepgram welcome: %s", welcome[:120] if isinstance(welcome, str) else "binary")

        await deepgram_ws.send(json.dumps(_deepgram_settings()))
        await _wait_for_settings_applied(deepgram_ws)
        await client_ws.send(json.dumps({"type": "ready"}))

        to_deepgram = asyncio.create_task(_recv_client(client_ws, deepgram_ws))
        to_client = asyncio.create_task(_recv_deepgram(deepgram_ws, client_ws))
        keepalive = asyncio.create_task(_send_deepgram_keepalive(deepgram_ws))
        done, pending = await asyncio.wait(
            {to_deepgram, to_client, keepalive},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()


async def _ws_handler(websocket):
    try:
        await _handle_client(websocket)
    except websockets.exceptions.ConnectionClosed:
        LOG.info("Voice connection closed")
    except Exception as exc:
        LOG.exception("Voice connection failed: %s", exc)
        try:
            await websocket.send(json.dumps({"type": "error", "message": str(exc)}))
        except Exception:
            pass


async def _serve_client_http():
    client_dir = Path(__file__).parent.parent / "client"

    async def index(_request):
        return web.Response(
            text=(client_dir / "interface.html").read_text(),
            content_type="text/html",
        )

    async def static(request):
        fname = request.match_info["filename"]
        fpath = client_dir / fname
        if not fpath.exists() or not fpath.is_file():
            return web.Response(status=404)
        return web.Response(
            body=fpath.read_bytes(),
            content_type="application/javascript" if fname.endswith(".js") else "text/plain",
        )

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/{filename}", static)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    LOG.info("Voice client HTTP server on port %d", HTTP_PORT)


async def main():
    LOG.info("Starting Deepgram voice bridge on %s:%d", HOST, PORT)
    async with websockets.serve(_ws_handler, HOST, PORT):
        await asyncio.gather(_serve_client_http(), asyncio.Future())


if __name__ == "__main__":
    asyncio.run(main())
