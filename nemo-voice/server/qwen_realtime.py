"""Qwen Omni Realtime bridge — entry point for the Qwen voice backend.

Thin routing layer: builds the session config and calls into qwen_pump for the
actual pump logic. Kept small so qwen_pump can stay under 300 lines on its own.
Selected via VOICE_BACKEND=qwen.
"""

from __future__ import annotations

import json
import logging
import os

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


def _voice_for(voice: str | None) -> str:
    return voice if voice in ALLOWED_VOICES else QWEN_VOICE


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
            "instructions": voice_brain.build_prompt(seed_history=True),
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
