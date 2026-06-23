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
from action_bridge import ActionBridge
from voice_metrics import METRICS


ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ROOT_ENV, override=False)
load_dotenv(override=False)

HOST = os.environ.get("VOICE_HOST", "0.0.0.0")
PORT = int(os.environ.get("VOICE_PORT", "3002"))
HTTP_PORT = int(os.environ.get("VOICE_HTTP_PORT", "3001"))
# Voice backend: "deepgram" (default) or "gemini" (Gemini Live API).
VOICE_BACKEND = os.environ.get("VOICE_BACKEND", "deepgram").strip().lower()
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


def _device_allowed(device_id: str) -> bool:
    """True unless NEMO_PAIRED_DEVICE_IDS is set and device_id isn't in it.
    Empty allowlist = accept any (token is still required)."""
    allow = {
        d.strip()
        for d in os.environ.get("NEMO_PAIRED_DEVICE_IDS", "").split(",")
        if d.strip()
    }
    return not allow or device_id in allow


def _tls_context():
    """TLS for both the voice WS and the HTTP client server. Set
    VOICE_TLS_CERT/VOICE_TLS_KEY (scripts/gen_server_cert.sh); the app pins
    the cert on first use. Returns None → plain ws/http (legacy)."""
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


# Aura-2 voices the app may pick per session. Whitelisted so a client can't
# inject an arbitrary model string into the Deepgram settings.
ALLOWED_VOICES = {
    "aura-2-draco-en",  # British male — "Jarvis" (default)
    "aura-2-orion-en",  # American male, warm
    "aura-2-apollo-en",  # American male, casual
    "aura-2-zeus-en",  # American male, authoritative
    "aura-2-arcas-en",  # American male, natural
    "aura-2-thalia-en",  # American female, bright
    "aura-2-luna-en",  # American female, soft
}


def _speak_model(voice: str | None) -> str:
    """The TTS voice for this session: the app's pick if it's allowed, else the
    env default, else Draco (Jarvis)."""
    if voice and voice in ALLOWED_VOICES:
        return voice
    return os.environ.get("DEEPGRAM_SPEAK_MODEL", "aura-2-draco-en")


def _deepgram_settings(voice: str | None = None) -> dict:
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
                    "eot_threshold": float(
                        os.environ.get("DEEPGRAM_EOT_THRESHOLD", "0.85")
                    ),
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
                "prompt": os.environ.get("DEEPGRAM_VOICE_PROMPT")
                or voice_brain.build_prompt(),
                # Client-side tools: read/write shared memory, reach Avazbek, time.
                "functions": voice_brain.FUNCTIONS,
            },
            "speak": {
                "provider": {
                    "type": "deepgram",
                    # Per-session voice (app picker) → env default → Draco
                    # (deep British male, the "Jarvis" voice).
                    "model": _speak_model(voice),
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


async def _handle_function_calls(event, deepgram_ws, bridge):
    """Run client-side tool calls Deepgram requested and return the results.

    Deepgram sends {"type":"FunctionCallRequest","functions":[{id,name,
    arguments(json-string),client_side}]}; we reply one FunctionCallResponse
    per call. Server-side (endpoint) functions are skipped — we declare none,
    so an unflagged call must not default to local execution.
    """
    for fn in event.get("functions", []):
        if not fn.get("client_side", False):
            continue
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        LOG.info("voice function call: %s %s", name, args)
        content = await voice_brain.dispatch(name, args, bridge)
        response = {
            "type": "FunctionCallResponse",
            "id": fn.get("id"),
            "name": name,
            "content": content,
        }
        if "thought_signature" in fn:
            response["thought_signature"] = fn["thought_signature"]
        await deepgram_ws.send(json.dumps(response))


async def _recv_deepgram(deepgram_ws, client_ws, bridge):
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
            await client_ws.send(
                json.dumps(
                    {
                        "type": "audio",
                        "data": base64.b64encode(message).decode("ascii"),
                    }
                )
            )
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
                await client_ws.send(
                    json.dumps(
                        {
                            "type": "user_transcript" if role == "user" else "text",
                            "data": text,
                        }
                    )
                )
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
            await client_ws.send(
                json.dumps({"type": "interrupted", "data": "barge_in"})
            )
        elif event_type == "FunctionCallRequest":
            await _handle_function_calls(event, deepgram_ws, bridge)
        elif event_type == "Error":
            LOG.error("Deepgram error: %s", event)
            await client_ws.send(
                json.dumps(
                    {
                        "type": "error",
                        "message": event.get("description")
                        or event.get("message")
                        or "Deepgram error",
                    }
                )
            )
        elif event_type == "Warning":
            LOG.warning("Deepgram warning: %s", event)
        elif event_type in {
            "Welcome",
            "AgentThinking",
            "AgentStartedSpeaking",
            "History",
        }:
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
            raise RuntimeError(
                event.get("description") or event.get("message") or str(event)
            )
        LOG.debug("Deepgram setup event: %s", event_type)


async def _recv_client(client_ws, deepgram_ws, bridge):
    total_audio = 0
    last_log = 0
    async for raw in client_ws:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            LOG.warning("Bad JSON from APK")
            continue

        msg_type = data.get("type")
        if msg_type == "action_result":
            bridge.resolve(data)
        elif msg_type == "audio":
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
                await deepgram_ws.send(
                    json.dumps(
                        {
                            "type": "InjectUserMessage",
                            "content": text,
                        }
                    )
                )


async def _send_deepgram_keepalive(deepgram_ws):
    while True:
        await asyncio.sleep(5)
        await deepgram_ws.send(json.dumps({"type": "KeepAlive"}))


async def _handle_client(client_ws):
    client_id = id(client_ws)
    expected_token = _required_env("NEMO_APP_TOKEN")

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

    # Optional device pinning: if NEMO_PAIRED_DEVICE_IDS is set (comma-separated),
    # only those device ids may open a voice session — so a leaked token alone,
    # from an unknown device, is not enough.
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

    # Backend switch: Qwen Omni Realtime, Gemini Live, or Deepgram (default).
    # All speak the same app protocol and share voice_brain (identity/memory/
    # tools). Deepgram code is kept intact below; set VOICE_BACKEND to switch.
    if VOICE_BACKEND == "qwen":
        import qwen_realtime

        await qwen_realtime.run_session(client_ws, auth.get("voice"))
        return

    if VOICE_BACKEND == "gemini":
        import gemini_streaming

        await gemini_streaming.run_session(client_ws)
        return

    deepgram_key = _required_env("DEEPGRAM_API_KEY")
    voice = auth.get("voice")
    headers = {"Authorization": f"Token {deepgram_key}"}
    async with websockets.connect(
        DEEPGRAM_URL, additional_headers=headers
    ) as deepgram_ws:
        welcome = await asyncio.wait_for(deepgram_ws.recv(), timeout=8)
        LOG.info(
            "Deepgram welcome: %s",
            welcome[:120] if isinstance(welcome, str) else "binary",
        )

        LOG.info("voice for session: %s", _speak_model(voice))
        await deepgram_ws.send(json.dumps(_deepgram_settings(voice)))
        await _wait_for_settings_applied(deepgram_ws)
        await client_ws.send(json.dumps({"type": "ready"}))

        bridge = ActionBridge(client_ws)
        to_deepgram = asyncio.create_task(_recv_client(client_ws, deepgram_ws, bridge))
        to_client = asyncio.create_task(_recv_deepgram(deepgram_ws, client_ws, bridge))
        keepalive = asyncio.create_task(_send_deepgram_keepalive(deepgram_ws))
        done, pending = await asyncio.wait(
            {to_deepgram, to_client, keepalive},
            return_when=asyncio.FIRST_COMPLETED,
        )
        # If the Deepgram side ended first the client is still up — tell it why
        # instead of letting the app see only a silent socket close.
        if to_client in done and to_deepgram not in done:
            try:
                await client_ws.send(
                    json.dumps(
                        {
                            "type": "error",
                            "message": "voice session ended (Deepgram closed)",
                        }
                    )
                )
            except Exception:
                pass
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()


async def _ws_handler(websocket):
    METRICS.session_start()
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
    finally:
        METRICS.session_end()


async def _serve_client_http(ssl_ctx=None):
    client_dir = Path(__file__).parent.parent / "client"

    async def index(_request):
        return web.Response(
            text=(client_dir / "interface.html").read_text(),
            content_type="text/html",
        )

    async def static(request):
        fname = request.match_info["filename"]
        fpath = (client_dir / fname).resolve()
        # Containment check: a URL-encoded ../ decodes into match_info, so an
        # unguarded join can escape client_dir and serve .env to anyone.
        if not fpath.is_relative_to(client_dir.resolve()):
            return web.Response(status=403)
        if not fpath.exists() or not fpath.is_file():
            return web.Response(status=404)
        return web.Response(
            body=fpath.read_bytes(),
            content_type="application/javascript"
            if fname.endswith(".js")
            else "text/plain",
        )

    async def health(request):
        # Liveness is unauthenticated so a probe / load balancer gets a cheap
        # 200. Operational metrics (session counts, latencies) require the app
        # token — on an internet-exposed port they should not be readable by
        # anyone who hits /health.
        token = os.environ.get("NEMO_APP_TOKEN", "").strip()
        auth = request.headers.get("Authorization", "")
        provided = auth[7:] if auth.startswith("Bearer ") else ""
        if token and hmac.compare_digest(provided, token):
            return web.json_response({**METRICS.snapshot(), "backend": VOICE_BACKEND})
        return web.json_response({"status": "ok"})

    app = web.Application()
    app.router.add_get("/", index)
    # /health must precede the /{filename} catch-all (aiohttp matches in order).
    app.router.add_get("/health", health)
    app.router.add_get("/{filename}", static)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT, ssl_context=ssl_ctx)
    await site.start()
    LOG.info("Voice client HTTP%s server on port %d", "S" if ssl_ctx else "", HTTP_PORT)


def _check_required_env() -> None:
    """Fail loudly at boot if the keys every session needs are missing.

    Without these, _handle_client raises per-connection and the app only sees
    a bare disconnect — the actual cause ("DEEPGRAM_API_KEY is not configured")
    never reaches the user. Surface it once, clearly, at startup.
    """
    required = ["NEMO_APP_TOKEN"]
    backend_key = {
        "qwen": "DASHSCOPE_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }.get(VOICE_BACKEND, "DEEPGRAM_API_KEY")
    required.append(backend_key)
    missing = [n for n in required if not os.environ.get(n, "").strip()]
    if missing:
        raise SystemExit(
            f"FATAL: missing required env: {', '.join(missing)}. "
            f"Set them in the .env next to this service before starting."
        )


async def main():
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
    async with websockets.serve(_ws_handler, HOST, PORT, ssl=ssl_ctx):
        await asyncio.gather(_serve_client_http(ssl_ctx), asyncio.Future())


if __name__ == "__main__":
    asyncio.run(main())
