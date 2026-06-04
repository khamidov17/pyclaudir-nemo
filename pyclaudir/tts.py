"""TTS — Gemini neural TTS (primary) with Edge TTS fallback.

Gemini TTS: gemini-2.5-flash-preview-tts — same model as claudir-main.
Fast, natural voice. Requires GEMINI_API_KEY in .env.

Edge TTS fallback: Microsoft neural TTS, no API key, works in China.

Outputs OGG Opus bytes (Telegram voice message format) or MP3.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import subprocess
import tempfile
import urllib.request

log = logging.getLogger("pyclaudir.tts")

_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
_VOICE = os.environ.get("NEMO_TTS_VOICE", "Kore")          # Gemini voice
_EDGE_VOICE = os.environ.get("NEMO_TTS_EDGE_VOICE", "en-US-AriaNeural")
_ENABLED = os.environ.get("NEMO_TTS_ENABLED", "true").lower() in {"1", "true", "yes"}


async def generate_ogg(text: str) -> bytes:
    """Generate OGG Opus audio (Telegram voice format) from text.

    Uses Gemini TTS if GEMINI_API_KEY is set, else Edge TTS.
    All subprocess/file I/O happens here — NOT inside tools/.
    """
    if _GEMINI_KEY:
        try:
            return await asyncio.to_thread(_gemini_to_ogg, text)
        except Exception as exc:
            log.warning("Gemini TTS failed, falling back to Edge TTS: %s", exc)
    return await _edge_to_ogg(text)


async def generate_mp3(text: str) -> bytes:
    """Generate MP3 audio (for phone app) from text."""
    return await _edge_mp3(text)


async def speak_to_app(text: str, app_clients: set) -> None:
    """Generate TTS audio and send to connected app clients (phone)."""
    if not _ENABLED or not app_clients or not text.strip():
        return
    try:
        audio_bytes = await generate_mp3(text)
    except Exception as exc:
        log.warning("TTS for app failed: %s", exc)
        return
    b64 = base64.b64encode(audio_bytes).decode("ascii")
    payload = json.dumps({"type": "audio", "data": b64, "format": "mp3"})
    dead: set = set()
    for ws in list(app_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    app_clients -= dead


# ── Gemini TTS ─────────────────────────────────────────────────────────────

def _gemini_to_ogg(text: str) -> bytes:
    """Gemini TTS → PCM → OGG Opus via ffmpeg. Mirrors claudir-main tts.rs."""
    if len(text) < 10:
        text = text + "."

    payload = json.dumps({
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": _VOICE}}
            },
        },
    }).encode()

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash-preview-tts:generateContent?key={_GEMINI_KEY}"
    )
    req = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    audio_b64 = (
        result["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    )
    pcm = base64.b64decode(audio_b64)
    return _pcm_to_ogg(pcm, 24000)


def _pcm_to_ogg(pcm: bytes, sample_rate: int = 24000) -> bytes:
    """Raw PCM (16-bit mono) → OGG Opus via ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        out = f.name
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "s16le", "-ar", str(sample_rate), "-ac", "1",
             "-i", "pipe:0", "-c:a", "libopus", "-b:a", "24k", out],
            input=pcm, capture_output=True, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode()[:200])
        data = open(out, "rb").read()
    finally:
        os.unlink(out)
    return data


def _mp3_to_ogg(mp3: bytes) -> bytes:
    """MP3 → OGG Opus via ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        out = f.name
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "24k", out],
            input=mp3, capture_output=True, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode()[:200])
        data = open(out, "rb").read()
    finally:
        os.unlink(out)
    return data


# ── Edge TTS ───────────────────────────────────────────────────────────────

async def _edge_mp3(text: str) -> bytes:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("edge-tts not installed") from exc
    communicate = edge_tts.Communicate(text=text, voice=_EDGE_VOICE)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    data = buf.getvalue()
    if not data:
        raise RuntimeError("Edge TTS returned empty audio")
    return data


async def _edge_to_ogg(text: str) -> bytes:
    mp3 = await _edge_mp3(text)
    return await asyncio.to_thread(_mp3_to_ogg, mp3)
