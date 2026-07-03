"""STT — transcribe audio files using Groq Whisper API.

Groq Whisper is the fastest Whisper implementation (~300ms latency).
Falls back to a message asking user to type if no API key.

Set GROQ_API_KEY in .env to enable.
Get free key at: console.groq.com
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("pyclaudir.stt")

_GROQ_KEY = os.environ.get("GROQ_API_KEY", "")


async def transcribe(audio_path: Path) -> str | None:
    """Transcribe an audio file (OGG/MP3/WAV) to text.

    Returns transcript string or None if unavailable.
    """
    if not _GROQ_KEY:
        return None
    if not audio_path.exists():
        return None

    try:
        import asyncio

        return await asyncio.to_thread(_groq_transcribe, audio_path)
    except Exception as exc:
        log.warning("STT transcription failed: %s", exc)
        return None


def _groq_transcribe(audio_path: Path) -> str:
    import httpx

    with open(audio_path, "rb") as f:
        resp = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {_GROQ_KEY}"},
            files={"file": (audio_path.name, f, "audio/ogg")},
            data={"model": "whisper-large-v3-turbo", "response_format": "text"},
            timeout=30,
        )
    resp.raise_for_status()
    return resp.text.strip()
