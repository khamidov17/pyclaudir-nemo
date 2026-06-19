"""Track Qwen realtime API spend from the per-response `usage` object.

Every Qwen `response.done` carries a usage object with audio/text token counts.
We accumulate them to data/qwen_usage.json and estimate cost from published
per-model rates, so the spend is visible without logging into the console.
The console Bill Details remain authoritative; this is a live running tally.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

LOG = logging.getLogger("nemo.qwen_usage")

# USD per 1,000,000 tokens (Alibaba Model Studio, international region).
# The "plus" realtime model is free during preview — tracked at $0 but token
# counts still accumulate so the tally is ready when billing turns on.
RATES: dict[str, dict[str, float]] = {
    "qwen3.5-omni-plus-realtime": {
        "audio_in": 0.0,
        "audio_out": 0.0,
        "text_in": 0.0,
        "text_out": 0.0,
        "preview": 1.0,
    },
    "qwen3-omni-flash-realtime": {
        "audio_in": 4.57,
        "audio_out": 18.13,
        "text_in": 1.99,
        "text_out": 3.67,
    },
    "qwen-omni-turbo-realtime": {
        "audio_in": 4.44,
        "audio_out": 8.89,
        "text_in": 1.07,
        "text_out": 2.52,
    },
}

_DATA_DIR = Path(
    os.environ.get("NEMO_VOICE_DATA_DIR")
    or (Path(__file__).resolve().parents[2] / "data")
)
_USAGE_FILE = _DATA_DIR / "qwen_usage.json"


def _split(details: dict) -> tuple[int, int]:
    """(audio_tokens, text_tokens) from an input/output token-details block."""
    return int(details.get("audio_tokens", 0)), int(details.get("text_tokens", 0))


def _cost(model: str, counts: dict[str, int]) -> float:
    r = RATES.get(model, {})
    return (
        counts["audio_in"] * r.get("audio_in", 0.0)
        + counts["audio_out"] * r.get("audio_out", 0.0)
        + counts["text_in"] * r.get("text_in", 0.0)
        + counts["text_out"] * r.get("text_out", 0.0)
    ) / 1_000_000


def _load() -> dict:
    try:
        return json.loads(_USAGE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {
            "turns": 0,
            "audio_in": 0,
            "audio_out": 0,
            "text_in": 0,
            "text_out": 0,
            "cost_usd": 0.0,
        }


def record(model: str, usage: dict) -> dict:
    """Add one response's usage to the running tally; persist and return it."""
    ain, _ = _split(usage.get("input_tokens_details", {}))
    aout, tout = _split(usage.get("output_tokens_details", {}))
    tin = max(int(usage.get("input_tokens", 0)) - ain, 0)  # text+image input
    counts = {"audio_in": ain, "audio_out": aout, "text_in": tin, "text_out": tout}
    total = _load()
    total["turns"] += 1
    for k, v in counts.items():
        total[k] += v
    total["cost_usd"] = round(total["cost_usd"] + _cost(model, counts), 6)
    total["model"] = model
    total["preview_free"] = bool(RATES.get(model, {}).get("preview"))
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _USAGE_FILE.write_text(json.dumps(total, indent=2))
    except OSError as exc:
        LOG.warning("could not persist qwen usage: %s", exc)
    return total
