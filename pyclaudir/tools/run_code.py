"""run_code — execute code in an isolated sandbox0 cloud sandbox.

The host's built-in bash/code tools are disabled (a voice-delegated or
injection-poisoned task could otherwise run code on the box that holds every API
key). This gives coding power back the safe way: the code runs in sandbox0's
isolated environment, never on the host. Inert until ``SANDBOX0_TOKEN`` is set —
then the engine can actually do the coding jobs voice Nemo delegates to it.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from .. import code_sandbox
from .base import BaseTool, ToolResult

log = logging.getLogger(__name__)

_MAX_OUTPUT = 8000
_ALLOWED_LANGS = {"python", "bash", "sh", "node"}


class RunCodeArgs(BaseModel):
    language: str = Field(
        default="python",
        description="Runtime: python, bash, or node.",
    )
    code: str = Field(
        description="Source to run in the sandbox. PRINT anything you want back "
        "— only stdout/output is returned.",
    )


class RunCodeTool(BaseTool):
    name = "run_code"
    description = (
        "Run code in an isolated cloud sandbox (sandbox0) — use this for any code "
        "execution; the host shell is intentionally disabled. Supports python, "
        "bash, node. Print what you want back. Each call is a fresh, throwaway "
        "sandbox with no access to this server."
    )
    args_model = RunCodeArgs

    async def run(self, args: RunCodeArgs) -> ToolResult:
        # Backstop: only the owner's turns may run code, independent of the
        # allowed-tool set (defense-in-depth vs a leaked tool on a webhook/
        # non-owner turn). Voice-delegated tasks fire as owner reminders.
        if not getattr(self.ctx, "owner_turn", True):
            return ToolResult(content="Code execution is owner-only.", is_error=True)
        if not code_sandbox.available():
            return ToolResult(
                content="Code sandbox not configured — set E2B_API_KEY (or "
                "SANDBOX0_TOKEN) to enable.",
                is_error=True,
            )
        lang = (args.language or "python").strip().lower()
        if lang not in _ALLOWED_LANGS:
            return ToolResult(
                content=f"Unsupported language '{lang}'. Use python, bash, or node.",
                is_error=True,
            )
        if not args.code.strip():
            return ToolResult(content="No code to run.", is_error=True)
        try:
            res = await asyncio.to_thread(code_sandbox.run_code_sync, lang, args.code)
        except Exception as exc:  # noqa: BLE001 — surface as a tool error, don't crash
            log.warning("run_code sandbox error: %s", exc)
            return ToolResult(content=f"Sandbox error: {exc}", is_error=True)
        output = (res.output or "(no output)")[:_MAX_OUTPUT]
        return ToolResult(content=output, is_error=not res.ok)
