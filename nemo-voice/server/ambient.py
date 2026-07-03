"""Ambient mode — Nemo hears the room, remembers, and stays quiet.

When ambient is ON, spoken turns are journaled into memory (owner-verified
voices only — a stranger in the room is NEVER stored) but Nemo does not reply
unless directly addressed. So an evening chat can reference what Avazbek said
at breakfast without him ever saying "remember this".

Feature gate: VOICE_AMBIENT=1 (allows the spoken toggle).
Per-session state lives on _SessionCtx.ambient_on; toggled by voice:
  on  — "ambient mode", "just listen", "shunchaki eshit"
  off — "ambient off", "stop listening mode", "normal rejim"

Addressed = his name in the utterance ("Nemo, …") — name variants tunable via
VOICE_ASSISTANT_NAMES (comma-separated, default "nemo,немо").
"""

from __future__ import annotations

import os
import re

_NAMES = [
    n.strip().lower()
    for n in os.environ.get("VOICE_ASSISTANT_NAMES", "nemo,немо").split(",")
    if n.strip()
]

_AMBIENT_ON = re.compile(
    r"\b(ambient\s+(mode|rejim)|just\s+listen|quiet\s+mode|"
    r"shunchaki\s+eshit|jim\s+eshit|passiv\s+rejim)\b",
    re.I,
)
_AMBIENT_OFF = re.compile(
    r"\b(ambient\s+(off|tugadi)|stop\s+listening\s+mode|normal\s+(mode|rejim)|"
    r"gaplashamiz|talk\s+to\s+me\s+again)\b",
    re.I,
)


def enabled() -> bool:
    return os.environ.get("VOICE_AMBIENT", "0").strip() == "1"


def is_on_intent(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 4 and bool(_AMBIENT_ON.search(t))


def is_off_intent(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 4 and bool(_AMBIENT_OFF.search(t))


def is_addressed(text: str) -> bool:
    """True when the utterance is aimed at Nemo (name mentioned)."""
    lowered = (text or "").lower()
    return any(re.search(rf"\b{re.escape(n)}\b", lowered) for n in _NAMES)
