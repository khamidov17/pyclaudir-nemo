"""semantic_router — map a user utterance to a RouteDecision tier.

Rules-first (wraps voice_intent) so the intent functions remain the single
source of truth. When VOICE_SHARED_CONTEXT is off this module is still
imported but route_tier() is always called — it just returns TIER0_VOICE for
chit-chat, and the Orchestrator ignores the decision.

TIER0 — Qwen handles directly (chit-chat, small tool calls).
TIER1 — reserved for a fast-reasoner (Haiku) — not wired yet.
TIER2 — route to the engine (CC) for code, planning, recall.
"""

from __future__ import annotations

from enum import Enum

import voice_intent


class RouteDecision(str, Enum):
    TIER0_VOICE = "tier0"
    TIER1_FAST = "tier1"
    TIER2_ENGINE = "tier2"


def route_tier(text: str) -> RouteDecision:
    """Return the routing tier for a finalized user utterance.

    Does NOT make network calls — pure keyword / regex logic.
    Complexity scales up as intent taxonomy grows; add new branches here.
    """
    if voice_intent.is_code_intent(text):
        return RouteDecision.TIER2_ENGINE
    if voice_intent.is_record_recall_intent(text):
        return RouteDecision.TIER2_ENGINE
    # Search is handled in-band by Qwen background tool — keep at TIER0.
    # Messages intent: background tool, stays TIER0.
    return RouteDecision.TIER0_VOICE
