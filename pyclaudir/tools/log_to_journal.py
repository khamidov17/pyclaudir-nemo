"""log_to_journal — Nemo's self-reporting tool.

Nemo calls this whenever he hits something he can't do or wants to flag for
the day's review. Appends to the same data/nemo_error_log.md that the voice
server writes to.
"""

from __future__ import annotations

from pydantic import Field

from ..error_journal import log_error, log_warn
from .base import BaseTool, ToolResult


class _Args:
    pass


from pydantic import BaseModel  # noqa: E402


class LogToJournalArgs(BaseModel):
    level: str = Field(
        description="'error' for something that failed or is broken; 'warn' for a degraded path that still partially worked.",
        pattern="^(error|warn)$",
    )
    source: str = Field(
        description="Short label like 'web_search', 'send_message', 'reminder', etc.",
        max_length=80,
    )
    message: str = Field(
        description="One sentence: what Nemo tried to do and what went wrong.",
        max_length=300,
    )
    detail: str = Field(
        default="",
        description="Optional: error text, exception message, or extra context.",
        max_length=500,
    )


class LogToJournalTool(BaseTool):
    name = "log_to_journal"
    description = (
        "Write an error or warning to the daily review journal. "
        "Call this when you couldn't complete something the user asked for, "
        "when a tool returned an error, or when you want to flag a problem "
        "for Avazbek to review later. Use level='error' for failures, "
        "'warn' for degraded paths."
    )
    args_model = LogToJournalArgs

    async def run(self, args: LogToJournalArgs) -> ToolResult:
        if args.level == "error":
            log_error(args.source, args.message, args.detail)
        else:
            log_warn(args.source, args.message, args.detail)
        return ToolResult(content=f"Logged [{args.level.upper()}] {args.source}: {args.message}")
