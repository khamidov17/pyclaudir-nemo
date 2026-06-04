"""SynthesizeMemoryWikiTool — aggregate all memory files into a structured wiki.

Reads every file from the memory store (up to *max_files*, skipping anything
over 64 KiB) and returns a single Markdown document the CC agent can use to
write or refresh ABOUT_ME.md.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Sequence

from pydantic import BaseModel, Field

from .base import BaseTool, ToolResult

_MAX_FILE_BYTES = 64 * 1024  # 64 KiB — mirrors MemoryStore's own cap
_log = logging.getLogger(__name__)

_ABOUT_ME_INSTRUCTIONS = """\
## Instructions for ABOUT_ME.md

Based on the above memories, write a comprehensive ABOUT_ME.md with these sections:

- **Identity** (name, role, location, languages)
- **Current Projects** (what you are building)
- **Preferences** (communication style, tech preferences, tools)
- **Relationships** (key people, collaborators)
- **Context** (anything else important)
"""


class SynthesizeMemoryWikiArgs(BaseModel):
    max_files: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of memory files to include.",
    )
    include_pattern: str | None = Field(
        default=None,
        description=(
            "Optional regex pattern. Only files whose relative path matches "
            "are included. Leave None to include everything."
        ),
    )


class SynthesizeMemoryWikiTool(BaseTool):
    name = "synthesize_memory_wiki"
    description = (
        "Read ALL memory files and return them as a structured wiki document. "
        "Use this to build or refresh ABOUT_ME.md — a comprehensive profile "
        "of the owner."
    )
    args_model = SynthesizeMemoryWikiArgs

    async def run(self, args: SynthesizeMemoryWikiArgs) -> ToolResult:
        store = self.ctx.memory_store
        if store is None:
            return ToolResult(content="memory store unavailable", is_error=True)

        all_files = await asyncio.to_thread(store.list)
        if not all_files:
            return ToolResult(content="(no memory files found)", is_error=False)

        selected = _filter_files(all_files, args.include_pattern, args.max_files)
        sections, skipped = await _read_sections(store, selected)

        wiki = _build_wiki(sections, total_listed=len(all_files), skipped=skipped)
        _log.info(
            "synthesize_memory_wiki: %d sections, %d skipped",
            len(sections),
            skipped,
        )
        return ToolResult(
            content=wiki,
            data={
                "files_included": len(sections),
                "files_skipped": skipped,
                "files_total": len(all_files),
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _filter_files(
    files: Sequence[object],
    pattern: str | None,
    max_files: int,
) -> list[object]:
    """Return up to *max_files* entries, optionally filtered by *pattern*."""
    if pattern is None:
        return list(files[:max_files])

    try:
        rx = re.compile(pattern)
    except re.error as exc:
        _log.warning(
            "synthesize_memory_wiki: invalid include_pattern %r: %s", pattern, exc
        )
        return list(files[:max_files])

    matched = [f for f in files if rx.search(str(getattr(f, "relative_path", f)))]
    return matched[:max_files]


async def _read_sections(
    store: object,
    files: list[object],
) -> tuple[list[tuple[str, str]], int]:
    """Read each file; return (path, content) pairs and a skip count."""
    sections: list[tuple[str, str]] = []
    skipped = 0

    for entry in files:
        path = str(getattr(entry, "relative_path", entry))
        size = getattr(entry, "size_bytes", 0)

        if size > _MAX_FILE_BYTES:
            _log.debug(
                "synthesize_memory_wiki: skipping %s (%d bytes > 64 KiB)", path, size
            )
            skipped += 1
            continue

        try:
            content: str = await asyncio.to_thread(store.read, path)  # type: ignore[union-attr]
        except Exception as exc:
            _log.warning("synthesize_memory_wiki: could not read %s: %s", path, exc)
            skipped += 1
            continue

        sections.append((path, content))

    return sections, skipped


def _build_wiki(
    sections: list[tuple[str, str]],
    total_listed: int,
    skipped: int,
) -> str:
    """Assemble the final Markdown document."""
    included = len(sections)
    lines: list[str] = [
        "## Memory Wiki",
        f"*Generated from {included} memory file(s)"
        + (f" — {skipped} skipped (too large or unreadable)" if skipped else "")
        + f" out of {total_listed} total*",
        "",
    ]

    for path, content in sections:
        lines.append(f"### {path}")
        lines.append("")
        lines.append(content.rstrip())
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(_ABOUT_ME_INSTRUCTIONS)
    return "\n".join(lines)
