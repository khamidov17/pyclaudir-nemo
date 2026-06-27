"""Tests for haiku_reasoner.call() and TIER1 routing.

haiku_reasoner reads VOICE_THINKER / ANTHROPIC_API_KEY at import time into
module globals, so we patch those globals rather than the environment.
"""

from __future__ import annotations

import asyncio

import haiku_reasoner
from semantic_router import RouteDecision, route_tier


def test_call_returns_none_when_thinker_disabled(monkeypatch) -> None:
    monkeypatch.setattr(haiku_reasoner, "_ENABLED", False)
    monkeypatch.setattr(haiku_reasoner, "_API_KEY", "sk-test")
    result = asyncio.run(haiku_reasoner.call("hello"))
    assert result is None


def test_call_returns_none_when_api_key_empty(monkeypatch) -> None:
    monkeypatch.setattr(haiku_reasoner, "_ENABLED", True)
    monkeypatch.setattr(haiku_reasoner, "_API_KEY", "")
    result = asyncio.run(haiku_reasoner.call("hello"))
    assert result is None


def test_tier1_matches_capital_question(monkeypatch) -> None:
    monkeypatch.setattr("semantic_router._TIERS_ENABLED", True)
    assert route_tier("what is the capital of France") == RouteDecision.TIER1_FAST


def test_tier1_does_not_match_play_music(monkeypatch) -> None:
    monkeypatch.setattr("semantic_router._TIERS_ENABLED", True)
    assert route_tier("play music") == RouteDecision.TIER0_VOICE
