"""Tests for semantic_router.route_tier()."""

from __future__ import annotations

import pytest

from semantic_router import RouteDecision, route_tier


@pytest.mark.parametrize(
    "text",
    [
        "write and run python to calculate the 20th fibonacci number",
        "run this code for me",
        "can you write a python script that sorts a list",
        "execute that",
        "debug this function please",
        "build a quick script to rename the files",
    ],
)
def test_code_intent_routes_tier2(text: str) -> None:
    assert route_tier(text) == RouteDecision.TIER2_ENGINE


@pytest.mark.parametrize(
    "text",
    [
        "summarize what we talked about",
        "give me a recap of the meeting",
        "send me the transcript",
        "what did we discuss in the recording",
        "read me the transcript",
    ],
)
def test_recall_intent_routes_tier2(text: str) -> None:
    assert route_tier(text) == RouteDecision.TIER2_ENGINE


@pytest.mark.parametrize(
    "text",
    [
        "tell me a joke",
        "how are you doing",
        "what's the weather today",
        "search for best laptops",
        "check my messages",
        "",
        "ok",
    ],
)
def test_chitchat_and_search_route_tier0(text: str) -> None:
    assert route_tier(text) == RouteDecision.TIER0_VOICE


def test_route_decision_values() -> None:
    assert RouteDecision.TIER0_VOICE.value == "tier0"
    assert RouteDecision.TIER1_FAST.value == "tier1"
    assert RouteDecision.TIER2_ENGINE.value == "tier2"


def test_returns_route_decision_type() -> None:
    result = route_tier("tell me a joke")
    assert isinstance(result, RouteDecision)
