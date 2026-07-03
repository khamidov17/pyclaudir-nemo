"""Live scene narration — an accessibility mode that describes surroundings.

Toggle on ("describe my surroundings" / "scene mode"), and the app streams a
camera frame every few seconds. Each frame is VL-described and a SHORT spoken
delta is woven into the session ("a door on your left, steps ahead"). Throttled
and dedup'd so it doesn't chatter; frames are never journaled.

See docs/design-vision-health.md.
"""

from __future__ import annotations

import logging
import os
import re
import time

import vision

LOG = logging.getLogger("nemo.narration")

_MIN_INTERVAL_S = float(os.environ.get("NARRATE_MIN_INTERVAL_S", "4"))

_ON = re.compile(
    r"\b(describe\s+(my\s+)?surroundings|scene\s+mode|narrat(e|ion)\s+mode|"
    r"what'?s\s+around\s+me|atrofni\s+ayt)\b",
    re.I,
)
_OFF = re.compile(
    r"\b(stop\s+describing|scene\s+mode\s+off|stop\s+narrat(ing|ion)|"
    r"atrofni\s+aytma)\b",
    re.I,
)

_PROMPT = (
    "You are guiding a visually-impaired person. Describe what's DIRECTLY "
    "useful for moving safely: obstacles, doors, stairs, people, signs, "
    "hazards, and roughly where they are (left/right/ahead). One short "
    "sentence, present tense, no preamble. If nothing notable, reply 'clear'."
)

_last_ts = 0.0
_last_desc = ""


def on_intent(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 4 and bool(_ON.search(t))


def is_off_intent(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 4 and bool(_OFF.search(t))


def reset() -> None:
    global _last_ts, _last_desc
    _last_ts = 0.0
    _last_desc = ""


def describe_frame(image_b64: str) -> str | None:
    """Describe a camera frame, throttled + deduped. None = say nothing now."""
    global _last_ts, _last_desc
    now = time.monotonic()
    if now - _last_ts < _MIN_INTERVAL_S:
        return None
    try:
        desc = vision.describe(image_b64, _PROMPT, "jpeg", 120)
    except Exception as exc:  # noqa: BLE001 — a bad frame must never crash the ws
        LOG.warning("narration describe failed: %s", exc)
        return None
    if not desc:
        return None  # don't consume the throttle window on a failed frame
    _last_ts = now
    desc = desc.strip()
    low = desc.lower()
    if low in ("clear", "clear.") or low == _last_desc:
        _last_desc = low
        return None
    _last_desc = low
    return desc
