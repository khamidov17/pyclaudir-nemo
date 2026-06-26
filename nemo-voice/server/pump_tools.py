"""Background tool helpers for the Qwen voice pump.

Extracted from qwen_pump.py to keep each file ≤300 lines.
All constants and tool-dispatch logic lives here so pump_class.py can import
without creating a circular dependency on qwen_pump.py.
"""

from __future__ import annotations

import json
import logging
import os

import qwen_usage
import reminders
import voice_brain
from qwen_link import QwenLink

LOG = logging.getLogger("nemo.qwen_pump")

_QWEN_MODEL = os.environ.get("QWEN_REALTIME_MODEL", "qwen3.5-omni-plus-realtime")
_BG_TOOLS: frozenset[str] = frozenset({"web_search", "look"})
_SENSITIVE_TOOLS: frozenset[str] = frozenset({"read_messages"})


# ── SessionCtx ────────────────────────────────────────────────────────────────

from dataclasses import dataclass  # noqa: E402


@dataclass
class _SessionCtx:
    """Bundle client_ws + bridge to keep _QwenPump.__init__ within 4 params."""

    client_ws: object
    bridge: object


# ── thin helpers ──────────────────────────────────────────────────────────────


def _bg_label(name: str, args: dict) -> str:
    return str(args.get("query") or args.get("task") or name)[:80]


def _deliver_via_engine(name: str, args: dict, content: str) -> None:
    """Fallback when the voice session is gone: push result to phone via engine."""
    try:
        parsed = json.loads(content)
        answer = parsed.get("result") or parsed.get("error") or content
    except Exception:  # noqa: BLE001
        answer = content
    reminders.notify_now(f"About '{_bg_label(name, args)}': {answer}")


async def _tool_output(link: QwenLink, call_id: str, content: str) -> None:
    await link.respond_to(
        {"type": "function_call_output", "call_id": call_id, "output": content}
    )


async def _run_bg_tool(link: QwenLink, bridge, name: str, args: dict) -> None:
    """Run a slow tool off the conversation, then inject its result to speak."""
    sensitive = name in _SENSITIVE_TOOLS
    try:
        content = await voice_brain.dispatch(name, args, bridge)
    except Exception as exc:  # noqa: BLE001
        content = json.dumps({"error": str(exc)})
    if sensitive:
        text = (
            "[Avazbek asked to check his messages. Give a SHORT, natural, "
            "Jarvis-style rundown — count, who, and the gist, one or two "
            "sentences. If it says 'nothing new', just say there's nothing new. "
            f"Result: {content}]"
        )
    else:
        text = (
            f"[Your background task '{_bg_label(name, args)}' just finished — tell "
            "Avazbek the answer now, briefly and naturally, one or two sentences. "
            f"Result: {content}]"
        )
    delivered = await link.inject_text_when_idle(text, sensitive=sensitive)
    if not delivered and not sensitive:
        _deliver_via_engine(name, args, content)


async def _handle_tool(link: QwenLink, bridge, item: dict) -> None:
    """Run a tool call and feed the result back so Nemo can keep talking."""
    name = item.get("name", "")
    call_id = item.get("call_id", "")
    try:
        args = json.loads(item.get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    LOG.info("qwen function call: %s %s", name, args)
    if name in _BG_TOOLS:
        await _tool_output(
            link,
            call_id,
            json.dumps(
                {"status": "on it — searching in the background, back in a sec"}
            ),
        )
        link.spawn_bg(lambda: _run_bg_tool(link, bridge, name, args))
        return
    content = await voice_brain.dispatch(name, args, bridge)
    await _tool_output(link, call_id, content)


def _track_usage(ev: dict) -> None:
    usage = ev.get("response", {}).get("usage")
    if not usage:
        return
    try:
        total = qwen_usage.record(_QWEN_MODEL, usage)
        LOG.info(
            "qwen usage: turn %d | cost so far $%.4f%s",
            total["turns"],
            total["cost_usd"],
            " (preview/free)" if total.get("preview_free") else "",
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("usage tracking failed: %s", exc)
