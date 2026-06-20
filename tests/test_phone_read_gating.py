"""Read-gating: screen/camera reads are refused on scheduler-fired turns.

Nemo must never capture the phone's screen or camera on its own — only in
direct response to a live user message. The engine sets
``ToolContext.user_initiated`` per turn (False for reminder/briefing turns).
"""

from __future__ import annotations

import pytest

from pyclaudir.tools.base import ToolContext
from pyclaudir.tools.phone_action import PhoneActionArgs, PhoneActionTool


class _FakeBroker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send_action(self, command: str) -> dict:
        self.calls.append(command)
        return {"ok": True, "text": "ok"}


@pytest.mark.asyncio
async def test_reads_blocked_on_scheduler_turn() -> None:
    broker = _FakeBroker()
    tool = PhoneActionTool(ToolContext(phone_broker=broker, user_initiated=False))
    for verb in ("screenshot", "ui_tree", "camera"):
        result = await tool.run(PhoneActionArgs(command=verb))
        # Benign refusal (not is_error → no engine retry loop), and it never
        # reached the device.
        assert result.is_error is False
        assert "only allowed when" in result.content
    assert broker.calls == []


@pytest.mark.asyncio
async def test_reads_allowed_on_user_turn() -> None:
    broker = _FakeBroker()
    tool = PhoneActionTool(ToolContext(phone_broker=broker, user_initiated=True))
    result = await tool.run(PhoneActionArgs(command="ui_tree"))
    assert result.is_error is False
    assert broker.calls == ["ui_tree"]


@pytest.mark.asyncio
async def test_actions_allowed_even_on_scheduler_turn() -> None:
    # Non-read actions (open/alarm) are NOT gated — only reads are.
    broker = _FakeBroker()
    tool = PhoneActionTool(ToolContext(phone_broker=broker, user_initiated=False))
    await tool.run(PhoneActionArgs(command="open org.telegram.messenger"))
    assert broker.calls == ["open org.telegram.messenger"]
