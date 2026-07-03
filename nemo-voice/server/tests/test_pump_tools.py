"""Integration tests for pump_tools._handle_tool weave-in wiring."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator import Orchestrator
import session_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    session_registry._registry.clear()
    yield
    session_registry._registry.clear()


def _make_orch(
    session_id: str = "aaaaaaaa-0000-4000-8000-000000000099",
) -> Orchestrator:
    link = MagicMock()
    link.sensitive_next = False
    link.inject_text_when_idle = AsyncMock(return_value=True)
    return Orchestrator(link=link, session_id=session_id)


@pytest.mark.asyncio
async def test_handle_tool_tags_delegate_with_session_id() -> None:
    """_handle_tool must inject _voice_session_id into delegate_task args when
    an orchestrator is present — catches dead-wiring where orchestrator was
    never passed from pump_class._maybe_tool."""
    from pump_tools import _handle_tool
    from qwen_link import QwenLink

    captured: list[dict] = []

    async def _fake_dispatch(name, args, bridge):
        captured.append({"name": name, "args": dict(args)})
        return json.dumps({"status": "ok"})

    with patch("pump_tools.voice_brain") as mock_brain:
        mock_brain.dispatch = _fake_dispatch

        link = MagicMock(spec=QwenLink)
        link.respond_to = AsyncMock()
        bridge = MagicMock()
        orch = _make_orch()
        item = {
            "type": "function_call",
            "name": "delegate_task",
            "call_id": "call_123",
            "arguments": json.dumps({"task": "write me a script"}),
        }
        await _handle_tool(link, bridge, item, orchestrator=orch)

    assert len(captured) == 1
    assert captured[0]["args"]["_voice_session_id"] == orch.session_id
    assert "_voice_rev" in captured[0]["args"]
    orch.close()


@pytest.mark.asyncio
async def test_handle_tool_no_tag_without_orchestrator() -> None:
    """Without orchestrator, delegate_task must NOT get _voice_session_id injected."""
    from pump_tools import _handle_tool
    from qwen_link import QwenLink

    captured: list[dict] = []

    async def _fake_dispatch(name, args, bridge):
        captured.append({"name": name, "args": dict(args)})
        return json.dumps({"status": "ok"})

    with patch("pump_tools.voice_brain") as mock_brain:
        mock_brain.dispatch = _fake_dispatch

        link = MagicMock(spec=QwenLink)
        link.respond_to = AsyncMock()
        bridge = MagicMock()
        item = {
            "type": "function_call",
            "name": "delegate_task",
            "call_id": "call_456",
            "arguments": json.dumps({"task": "do something"}),
        }
        await _handle_tool(link, bridge, item, orchestrator=None)

    assert len(captured) == 1
    assert "_voice_session_id" not in captured[0]["args"]
