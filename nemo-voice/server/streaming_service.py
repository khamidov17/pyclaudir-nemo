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

import voice_brain


ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ROOT_ENV, override=False)
load_dotenv(override=False)

HOST = os.environ.get("VOICE_HOST", "0.0.0.0")
PORT = int(os.environ.get("VOICE_PORT", "3002"))
HTTP_PORT = int(os.environ.get("VOICE_HTTP_PORT", "3001"))
DEEPGRAM_URL = "wss://agent.deepgram.com/v1/agent/converse"
DEEPGRAM_AUDIO_DONE_FALLBACK_SEC = float(
    os.environ.get("DEEPGRAM_AUDIO_DONE_FALLBACK_SEC", "1.5")
)

LOG = logging.getLogger("nemo.deepgram_voice")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


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
                    "eot_threshold": float(os.environ.get("DEEPGRAM_EOT_THRESHOLD", "0.85")),
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
                # Nemo identity + the shared memory store, rebuilt each session.
                "prompt": os.environ.get("DEEPGRAM_VOICE_PROMPT") or voice_brain.build_prompt(),
                # Client-side tools: read/write shared memory, reach Avazbek, time.
                "functions": voice_brain.FUNCTIONS,
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


async def _handle_function_calls(event, deepgram_ws):
    """Run client-side tool calls Deepgram requested and return the results.

    Deepgram sends {"type":"FunctionCallRequest","functions":[{id,name,
    arguments(json-string),client_side}]}; we reply one FunctionCallResponse
    per call. Server-side (endpoint) functions are skipped — we declare none.
    """
    for fn in event.get("functions", []):
        if not fn.get("client_side", True):
            continue
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        LOG.info("voice function call: %s %s", name, args)
        content = await voice_brain.dispatch(name, args)
        response = {
            "type": "FunctionCallResponse",
            "id": fn.get("id"),
            "name": name,
            "content": content,
        }
        if "thought_signature" in fn:
            response["thought_signature"] = fn["thought_signature"]
        await deepgram_ws.send(json.dumps(response))


async def _recv_deepgram(deepgram_ws, client_ws):
    audio_done_timer = None
    agent_audio_started = False
    audio_bytes = 0

    async def complete_audio_turn(delay=0.0):
        nonlocal agent_audio_started, audio_bytes
        if delay:
            await asyncio.sleep(delay)
        if agent_audio_started:
            LOG.info("agent audio turn complete: %d bytes", audio_bytes)
            await client_ws.send(json.dumps({"type": "turn_complete"}))
            agent_audio_started = False
            audio_bytes = 0

    def schedule_audio_done():
        nonlocal audio_done_timer
        if audio_done_timer:
            audio_done_timer.cancel()
        audio_done_timer = asyncio.create_task(
            complete_audio_turn(DEEPGRAM_AUDIO_DONE_FALLBACK_SEC)
        )

    async for message in deepgram_ws:
        if isinstance(message, bytes):
            if not agent_audio_started:
                agent_audio_started = True
                await client_ws.send(json.dumps({"type": "agent_audio_start"}))
            audio_bytes += len(message)
            await client_ws.send(json.dumps({
                "type": "audio",
                "data": base64.b64encode(message).decode("ascii"),
            }))
            schedule_audio_done()
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
            if audio_done_timer:
                audio_done_timer.cancel()
                audio_done_timer = None
            await complete_audio_turn()
        elif event_type == "UserStartedSpeaking":
            if audio_done_timer:
                audio_done_timer.cancel()
                audio_done_timer = None
            agent_audio_started = False
            audio_bytes = 0
            await client_ws.send(json.dumps({"type": "interrupted", "data": "barge_in"}))
        elif event_type == "FunctionCallRequest":
            await _handle_function_calls(event, deepgram_ws)
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
        # If the Deepgram side ended first the client is still up — tell it why
        # instead of letting the app see only a silent socket close.
        if to_client in done and to_deepgram not in done:
            try:
                await client_ws.send(json.dumps(
                    {"type": "error", "message": "voice session ended (Deepgram closed)"}
                ))
            except Exception:
                pass
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


def _check_required_env() -> None:
    """Fail loudly at boot if the keys every session needs are missing.

    Without these, _handle_client raises per-connection and the app only sees
    a bare disconnect — the actual cause ("DEEPGRAM_API_KEY is not configured")
    never reaches the user. Surface it once, clearly, at startup.
    """
    missing = [n for n in ("NEMO_APP_TOKEN", "DEEPGRAM_API_KEY") if not os.environ.get(n, "").strip()]
    if missing:
        raise SystemExit(
            f"FATAL: missing required env: {', '.join(missing)}. "
            f"Set them in the .env next to this service before starting."
        )


async def main():
    _check_required_env()
    LOG.info("Starting Deepgram voice bridge on %s:%d", HOST, PORT)
    async with websockets.serve(_ws_handler, HOST, PORT):
        await asyncio.gather(_serve_client_http(), asyncio.Future())


if __name__ == "__main__":
    asyncio.run(main())
