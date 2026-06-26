"""End-to-end weave-in round-trip — the integration test that was missing.

The unit tests set `rev` by hand, so they never exercised the actual contract
between the engine (which ECHOES the delegate-time snapshot.rev unchanged) and
the orchestrator (which drops chunks whose rev is older than the current
snapshot). That gap hid two real bugs:

  1. delegate never carried the session_id → the engine never streamed;
  2. the engine fabricated its own per-chunk rev → every chunk was dropped as
     "stale" because that counter is unrelated to snapshot.rev.

These tests reproduce the real flow.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import session_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    session_registry._registry.clear()
    yield
    session_registry._registry.clear()
    # Restore orchestrator's module-level flags to default (off) after tests
    # that reloaded it under a patched env.
    import orchestrator

    importlib.reload(orchestrator)


def _link():
    link = MagicMock()
    link.sensitive_next = False
    link.inject_text_when_idle = AsyncMock(return_value=True)
    return link


def _weave_orch(session_id: str):
    """An Orchestrator with VOICE_WEAVE_IN=1 active (module reloaded under env)."""
    import orchestrator

    importlib.reload(orchestrator)
    return orchestrator.Orchestrator(link=_link(), session_id=session_id)


# ── the rev round-trip (the headline bug) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_chunk_with_delegate_rev_is_spoken() -> None:
    """Engine echoes the rev captured at delegate time; while the topic is
    unchanged that chunk must be DELIVERED, not dropped as stale."""
    with patch.dict(os.environ, {"VOICE_WEAVE_IN": "1"}):
        orch = _weave_orch("aaaaaaaa-0000-4000-8000-000000000010")
        await orch.on_final_transcript("write code to sort a list")
        delegate_rev = orch.snapshot.rev  # exactly what the engine is handed

        await orch.on_background_chunk("Here is the sorted list.", True, delegate_rev)

        orch.link.inject_text_when_idle.assert_awaited()
        sent = orch.link.inject_text_when_idle.await_args.args[0]
        assert "sorted list" in sent
        orch.close()


@pytest.mark.asyncio
async def test_chunk_dropped_after_new_user_turn() -> None:
    """If the user moved on (snapshot.rev advanced) before the answer arrives,
    the stale chunk is dropped."""
    with patch.dict(os.environ, {"VOICE_WEAVE_IN": "1"}):
        orch = _weave_orch("aaaaaaaa-0000-4000-8000-000000000011")
        await orch.on_final_transcript("write code to sort a list")
        delegate_rev = orch.snapshot.rev
        await orch.on_final_transcript("never mind, what's the weather")  # new turn

        await orch.on_background_chunk("Here is the sorted list.", True, delegate_rev)

        orch.link.inject_text_when_idle.assert_not_awaited()
        orch.close()


# ── ordering (the create_task-per-POST scramble) ─────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_preserves_clause_order() -> None:
    with patch.dict(os.environ, {"VOICE_WEAVE_IN": "1"}):
        orch = _weave_orch("aaaaaaaa-0000-4000-8000-000000000012")
        rev = orch.snapshot.rev
        for clause in ("First clause.", "Second clause.", "Third clause."):
            orch.enqueue_chunk(clause, False, rev)
        await asyncio.sleep(0.05)  # let the single consumer drain in order

        spoken = [c.args[0] for c in orch.link.inject_text_when_idle.await_args_list]
        joined = " || ".join(spoken)
        assert joined.index("First") < joined.index("Second") < joined.index("Third")
        orch.close()


# ── delegate tagging (the missing entry point) ───────────────────────────────


@pytest.mark.asyncio
async def test_handle_tool_tags_delegate_with_session_and_rev() -> None:
    """_handle_tool must inject _voice_session_id + _voice_rev so the engine can
    stream the result back to THIS session."""
    import pump_tools

    captured: dict = {}

    async def fake_dispatch(name, args, bridge):
        captured["name"] = name
        captured["args"] = dict(args)
        return json.dumps({"status": "delegated"})

    link = MagicMock()
    link.respond_to = AsyncMock()
    orch = MagicMock()
    orch.session_id = "aaaaaaaa-0000-4000-8000-000000000013"
    orch.snapshot.rev = 7

    item = {
        "name": "delegate_task",
        "call_id": "c1",
        "arguments": json.dumps({"task": "build me a thing"}),
    }
    with patch.object(pump_tools.voice_brain, "dispatch", fake_dispatch):
        await pump_tools._handle_tool(link, None, item, orch)

    assert captured["name"] == "delegate_task"
    assert captured["args"]["_voice_session_id"] == orch.session_id
    assert captured["args"]["_voice_rev"] == 7
