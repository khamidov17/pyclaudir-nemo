"""Deepgram session settings builder — extracted from deepgram_bridge.py.

Keeps deepgram_bridge.py under 300 lines.
"""

from __future__ import annotations

import os

import voice_brain

ALLOWED_VOICES = {
    "aura-2-draco-en",
    "aura-2-orion-en",
    "aura-2-apollo-en",
    "aura-2-zeus-en",
    "aura-2-arcas-en",
    "aura-2-thalia-en",
    "aura-2-luna-en",
}


def speak_model(voice: str | None) -> str:
    if voice and voice in ALLOWED_VOICES:
        return voice
    return os.environ.get("DEEPGRAM_SPEAK_MODEL", "aura-2-draco-en")


def _agent_cfg(voice: str | None) -> dict:
    return {
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
            "prompt": os.environ.get("DEEPGRAM_VOICE_PROMPT")
            or voice_brain.build_prompt(),
            "functions": voice_brain.FUNCTIONS,
        },
        "speak": {
            "provider": {"type": "deepgram", "model": speak_model(voice)},
        },
    }


def build_settings(voice: str | None = None) -> dict:
    """Build the full Deepgram Settings payload."""
    agent = _agent_cfg(voice)
    greeting = os.environ.get("DEEPGRAM_GREETING", "").strip()
    if greeting:
        agent["greeting"] = greeting
    return {
        "type": "Settings",
        "tags": ["nemo", "android"],
        "mip_opt_out": True,
        "audio": {
            "input": {"encoding": "linear16", "sample_rate": 16000},
            "output": {
                "encoding": "linear16",
                "sample_rate": 24000,
                "container": "none",
            },
        },
        "agent": agent,
    }
