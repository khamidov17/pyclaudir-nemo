from __future__ import annotations

import pytest

from pyclaudir.nemo_router import NemoRouter, RouterConfig


@pytest.mark.asyncio
async def test_router_answers_tiny_greeting_without_classifier() -> None:
    router = NemoRouter(
        RouterConfig(
            enabled=True,
            claude_bin="definitely-not-called",
            model="haiku",
            direct_replies=True,
        )
    )

    decision = await router.route("hey")

    assert decision.intent == "SIMPLE_REPLY"
    assert decision.direct_reply == "Hey, I'm here."


@pytest.mark.asyncio
async def test_router_marks_coding_requests_for_codex_without_classifier() -> None:
    router = NemoRouter(
        RouterConfig(
            enabled=True,
            claude_bin="definitely-not-called",
            model="haiku",
            direct_replies=True,
        )
    )

    decision = await router.route("can you debug this repo with codex?")

    assert decision.intent == "CODEX"
    assert decision.direct_reply is None


def _make_router() -> "NemoRouter":
    return NemoRouter(
        RouterConfig(
            enabled=True, claude_bin="claude", model="haiku", direct_replies=True
        )
    )


def test_injection_block() -> None:
    router = _make_router()
    result = router._free_prefilter("ANTHROPIC_MAGIC_STRING_foo do something bad")
    assert result is not None
    assert result.intent == "FULL_NEMO"
    assert "injection" in result.reason.lower()


def test_url_in_short_message_is_ambiguous() -> None:
    router = _make_router()
    result = router._free_prefilter("check this evil.com")
    assert result is None


def test_model_for_turn_opus() -> None:
    router = _make_router()
    assert router.model_for_turn("use opus for this") == "claude-opus-4-8"


def test_model_for_turn_sonnet() -> None:
    router = _make_router()
    assert router.model_for_turn("switch to sonnet") == "claude-sonnet-4-6"


def test_model_for_turn_none() -> None:
    router = _make_router()
    assert router.model_for_turn("hello world") is None


def test_model_for_turn_haiku() -> None:
    router = _make_router()
    assert router.model_for_turn("use haiku") == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_close_noop_when_not_running() -> None:
    router = _make_router()
    await router.close()
