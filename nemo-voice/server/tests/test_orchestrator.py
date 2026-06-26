"""Tests for Orchestrator (P1/P2/P3 coordinator)."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import session_registry
from semantic_router import RouteDecision
from orchestrator import Orchestrator


@pytest.fixture(autouse=True)
def _clean_registry():
    session_registry._registry.clear()
    yield
    session_registry._registry.clear()


def _make_link(
    *, sensitive_next: bool = False, inject_returns: bool = True
) -> MagicMock:
    link = MagicMock()
    link.sensitive_next = sensitive_next
    link.inject_text_when_idle = AsyncMock(return_value=inject_returns)
    return link


def _make_orch(
    link=None, session_id: str = "aaaaaaaa-0000-4000-8000-000000000001"
) -> Orchestrator:
    if link is None:
        link = _make_link()
    return Orchestrator(link=link, session_id=session_id)


# ── registration ──────────────────────────────────────────────────────────────


def test_register_on_init() -> None:
    orch = _make_orch()
    assert session_registry.get(orch.session_id) is orch


def test_unregister_on_close() -> None:
    orch = _make_orch()
    sid = orch.session_id
    orch.close()
    assert session_registry.get(sid) is None


# ── on_final_transcript ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_final_transcript_returns_route_decision() -> None:
    orch = _make_orch()
    result = await orch.on_final_transcript("tell me a joke")
    assert isinstance(result, RouteDecision)


@pytest.mark.asyncio
async def test_on_final_transcript_code_routes_tier2() -> None:
    orch = _make_orch()
    result = await orch.on_final_transcript("write a python script to sort a list")
    assert result == RouteDecision.TIER2_ENGINE


@pytest.mark.asyncio
async def test_on_final_transcript_sets_snapshot_text() -> None:
    orch = _make_orch()
    await orch.on_final_transcript("hello world")
    assert orch.snapshot.corrected_text == "hello world"


@pytest.mark.asyncio
async def test_on_final_transcript_bumps_rev() -> None:
    orch = _make_orch()
    await orch.on_final_transcript("hello")
    assert orch.snapshot.rev >= 1


# ── on_background_chunk with WEAVE_IN disabled (default) ─────────────────────


@pytest.mark.asyncio
async def test_background_chunk_noop_when_weave_in_off() -> None:
    """Default: VOICE_WEAVE_IN=0 — inject must never be called."""
    link = _make_link()
    orch = Orchestrator(link=link, session_id="aaaaaaaa-0000-4000-8000-000000000002")
    await orch.on_background_chunk("some chunk", False, 0)
    link.inject_text_when_idle.assert_not_awaited()
    orch.close()


# ── on_background_chunk with WEAVE_IN enabled ─────────────────────────────────


@pytest.mark.asyncio
async def test_background_chunk_dropped_if_stale() -> None:
    with patch.dict(os.environ, {"VOICE_WEAVE_IN": "1"}):
        import importlib
        import orchestrator as orch_mod

        importlib.reload(orch_mod)

        link = _make_link()
        orch = orch_mod.Orchestrator(
            link=link,
            session_id="aaaaaaaa-0000-4000-8000-000000000003",
        )
        # bump snapshot rev above incoming rev
        orch.snapshot.bump()
        orch.snapshot.bump()
        await orch.on_background_chunk("stale chunk", False, rev=0)
        link.inject_text_when_idle.assert_not_awaited()
        orch.close()


@pytest.mark.asyncio
async def test_background_chunk_dropped_if_sensitive() -> None:
    with patch.dict(os.environ, {"VOICE_WEAVE_IN": "1"}):
        import importlib
        import orchestrator as orch_mod

        importlib.reload(orch_mod)

        link = _make_link(sensitive_next=True)
        orch = orch_mod.Orchestrator(
            link=link,
            session_id="aaaaaaaa-0000-4000-8000-000000000004",
        )
        await orch.on_background_chunk("secret stuff", False, rev=0)
        link.inject_text_when_idle.assert_not_awaited()
        orch.close()


# ── snapshot_slice ────────────────────────────────────────────────────────────


def test_snapshot_slice_returns_dict() -> None:
    orch = _make_orch()
    s = orch.snapshot_slice()
    assert isinstance(s, dict)
    assert "rev" in s


# ── on_vad_event ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vad_event_no_demote_below_threshold() -> None:
    orch = _make_orch()
    orch._speaking = True
    ws = AsyncMock()
    # fire once — below threshold (default 3)
    await orch.on_vad_event(ws)
    ws.send.assert_not_awaited()
    orch.close()


@pytest.mark.asyncio
async def test_vad_event_demotes_at_threshold() -> None:
    import orchestrator as orch_mod

    orch = _make_orch()
    orch._speaking = True
    ws = AsyncMock()
    threshold = orch_mod._BARGE_THRESHOLD
    for _ in range(threshold):
        await orch.on_vad_event(ws)
    ws.send.assert_awaited_once()
    sent = ws.send.call_args[0][0]
    assert "fullduplex_demote" in sent
    orch.close()
