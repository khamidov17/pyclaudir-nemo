"""Qwen Omni Realtime bridge — entry point for the Qwen voice backend.

Thin routing layer: builds the session config and calls into qwen_pump for the
actual pump logic. Kept small so qwen_pump can stay under 300 lines on its own.
Selected via VOICE_BACKEND=qwen.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

import voice_brain
from qwen_pump import _QwenConnCfg, run_qwen_session

LOG = logging.getLogger("nemo.qwen_realtime")

QWEN_URL = os.environ.get(
    "QWEN_REALTIME_URL", "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
)
QWEN_MODEL = os.environ.get("QWEN_REALTIME_MODEL", "qwen3.5-omni-plus-realtime")
QWEN_VOICE = os.environ.get("QWEN_VOICE", "Ethan")
ALLOWED_VOICES = {
    "Ethan",
    "Ryan",
    "Dylan",
    "Aiden",
    "Tina",
    "Serena",
    "Jennifer",
    "Sunny",
}
_TRANSCRIBE_MODEL = os.environ.get("QWEN_TRANSCRIBE_MODEL", "gummy-realtime-v1")

_VOICE_ORCHESTRATOR = os.environ.get("VOICE_ORCHESTRATOR", "0").strip() == "1"
_PERSONA = os.environ.get("VOICE_PERSONA", "0").strip() == "1"

# Same resolution as reminders.py _DATA_DIR; points at memory_index.db
# (semantic_memory's store), NOT pyclaudir.db which has no memory chunks.
_DATA_DIR: Path = (
    Path(os.environ.get("NEMO_VOICE_DATA_DIR", ""))
    if os.environ.get("NEMO_VOICE_DATA_DIR")
    else Path(__file__).resolve().parents[2] / "data"
)
_MEMORY_DB: Path = _DATA_DIR / "memory_index.db"


def _voice_for(voice: str | None) -> str:
    return voice if voice in ALLOWED_VOICES else QWEN_VOICE


def _load_persona() -> dict | None:
    """Load user profile from memory_index.db (semantic_memory's store).

    Voice server is a SEPARATE PROCESS from pyclaudir — cannot import pyclaudir.
    Sync — called only once at session start, so blocking is acceptable.
    """
    if not _MEMORY_DB.exists():
        return None
    try:
        con = sqlite3.connect(str(_MEMORY_DB), timeout=2.0)
        rows = con.execute(
            "SELECT text FROM chunks WHERE source='memory' ORDER BY rowid DESC LIMIT 30"
        ).fetchall()
        con.close()
        for (text,) in rows:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(data, dict)
                and "name" in data
                and ("tone" in data or "topics" in data)
            ):
                return data
        return None
    except Exception as exc:  # noqa: BLE001 — best-effort load, never block session
        LOG.debug("persona load skipped: %s", exc)
        return None


def _build_persona_block(profile: dict) -> str:
    """Build a safe persona instruction string from a profile dict."""
    import re

    def _safe(s: str, max_len: int) -> str:
        return re.sub(r"<[^>]{0,80}>", "", str(s))[:max_len]

    name = _safe(profile.get("name", "the user"), 50)
    tone = _safe(profile.get("tone", "friendly and direct"), 80)
    topics = [_safe(t, 40) for t in (profile.get("topics") or [])[:5]]
    return f"You are Nemo, a personal AI assistant for {name}. Tone: {tone}. " + (
        f"Recent topics: {', '.join(topics)}. " if topics else ""
    )


def _with_persona(instructions: str) -> str:
    """Prepend persona block to instructions when VOICE_PERSONA is on."""
    if not _PERSONA:
        return instructions
    profile = _load_persona()
    if not profile:
        return instructions
    persona = _build_persona_block(profile)
    return persona + ("\n\n" + instructions if instructions else "")


def _tools() -> list[dict]:
    return [
        {
            "type": "function",
            "name": f["name"],
            "description": f["description"],
            "parameters": f.get("parameters") or {"type": "object", "properties": {}},
        }
        for f in voice_brain.FUNCTIONS
    ]


def _session_config(voice: str) -> dict:
    return {
        "type": "session.update",
        "session": {
            "modalities": ["text", "audio"],
            "voice": voice,
            "instructions": _with_persona(voice_brain.build_prompt(seed_history=True)),
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "input_audio_transcription": {"model": _TRANSCRIBE_MODEL},
            "turn_detection": {
                "type": "server_vad",
                "threshold": float(os.environ.get("QWEN_VAD_THRESHOLD", "0.35")),
                "silence_duration_ms": int(
                    os.environ.get("QWEN_VAD_SILENCE_MS", "1200")
                ),
            },
            "tools": _tools(),
        },
    }


async def run_session(client_ws, voice: str | None = None) -> None:
    """Bridge one authenticated app client to a Qwen Omni Realtime session."""
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        await client_ws.send(
            json.dumps({"type": "error", "message": "DASHSCOPE_API_KEY not configured"})
        )
        return
    chosen = _voice_for(voice)
    orch_factory = None
    if _VOICE_ORCHESTRATOR:
        from orchestrator import make_orchestrator

        orch_factory = make_orchestrator
    conn_cfg = _QwenConnCfg(
        api_key=api_key,
        url=QWEN_URL,
        model=QWEN_MODEL,
        session_config=_session_config(chosen),
        voice=chosen,
    )
    await run_qwen_session(
        client_ws,
        cfg=conn_cfg,
        orchestrator_factory=orch_factory,
    )
