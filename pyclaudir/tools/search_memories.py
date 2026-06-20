"""search_memories — grep-style search across all memory files with wikilink resolution."""

from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, Field

from .. import semantic_memory
from .base import BaseTool, ToolResult

_log = logging.getLogger(__name__)
_CONTEXT_LINES = 2
_MAX_RESULTS = 40
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


class SearchMemoriesArgs(BaseModel):
    query: str = Field(description="Search term (or natural-language question).")
    use_regex: bool = Field(
        default=False, description="Treat query as a regex pattern (keyword mode only)."
    )
    semantic: bool = Field(
        default=True,
        description="Also rank memories by MEANING (finds 'dark roast' for 'coffee').",
    )
    max_results: int = Field(default=_MAX_RESULTS, ge=1, le=200)


class SearchMemoriesTool(BaseTool):
    name = "search_memories"
    description = (
        "Search memory by meaning AND keyword. Semantic ranking finds relevant "
        "facts even with no shared words ('what does he drive?' → 'has a Tesla'); "
        "keyword/regex catches exact terms. Resolves [[wikilinks]] in matches."
    )
    args_model = SearchMemoriesArgs

    async def run(self, args: SearchMemoriesArgs) -> ToolResult:
        store = self.ctx.memory_store
        if store is None:
            return ToolResult(content="memory store unavailable", is_error=True)

        try:
            pattern = (
                re.compile(args.query, re.IGNORECASE)
                if args.use_regex
                else re.compile(re.escape(args.query), re.IGNORECASE)
            )
        except re.error as exc:
            return ToolResult(content=f"invalid regex: {exc}", is_error=True)

        results = await asyncio.to_thread(
            _search_files, store, pattern, args.max_results
        )
        sem = await _semantic(args.query) if args.semantic else []

        if not results and not sem:
            return ToolResult(content=f"No matches for {args.query!r}")

        linked = (
            await asyncio.to_thread(_resolve_wikilinks, store, results)
            if results
            else {}
        )
        content = _render(args.query, sem, results, linked)
        return ToolResult(
            content=content,
            data={"semantic": sem, "matches": results, "linked": linked},
        )


async def _semantic(query: str) -> list[dict]:
    """Meaning-ranked memory matches, or [] if embeddings are unavailable."""
    if not semantic_memory.available():
        return []
    return await asyncio.to_thread(semantic_memory.search, query, 8)


def _render(query: str, sem: list[dict], results: list[dict], linked: dict) -> str:
    lines: list[str] = []
    if sem:
        lines.append(f"Most relevant to {query!r} (by meaning):")
        lines.extend(f"- {m['text']}  ({m['ref']})" for m in sem)
        lines.append("")
    if results:
        lines.append(f"Keyword matches ({len(results)}):")
        for match in results:
            lines.append(f"### {match['file']}")
            lines.append(match["context"])
            lines.append("")
    if linked:
        lines.append("### Linked files (via wikilinks)")
        for path, snippet in linked.items():
            lines.append(f"**[[{path}]]**\n{snippet}\n")
    return "\n".join(lines)


def _search_files(store, pattern: re.Pattern, max_results: int) -> list[dict]:
    matches: list[dict] = []
    for mem_file in store.list():
        if len(matches) >= max_results:
            break
        try:
            text = store.read(mem_file.relative_path)
        except Exception:
            continue
        file_matches = _find_matches(mem_file.relative_path, text, pattern)
        remaining = max_results - len(matches)
        matches.extend(file_matches[:remaining])
    return matches


def _find_matches(filename: str, text: str, pattern: re.Pattern) -> list[dict]:
    file_matches: list[dict] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not pattern.search(line):
            continue
        start = max(0, i - _CONTEXT_LINES)
        end = min(len(lines), i + _CONTEXT_LINES + 1)
        context_lines = []
        for j in range(start, end):
            prefix = "→ " if j == i else "  "
            context_lines.append(f"{prefix}{j + 1}: {lines[j]}")
        file_matches.append(
            {"file": filename, "line": i + 1, "context": "\n".join(context_lines)}
        )
    return file_matches


def _resolve_wikilinks(store, matches: list[dict]) -> dict[str, str]:
    """Find [[wikilinks]] in match contexts and return first-line snippets."""
    seen: set[str] = set()
    linked: dict[str, str] = {}

    for match in matches:
        for wikilink in _WIKILINK_RE.findall(match["context"]):
            path = wikilink.strip()
            # Normalise: strip leading ./ and add .md if missing
            if not path.endswith(".md"):
                path = path + ".md"
            if path in seen:
                continue
            seen.add(path)
            try:
                text = store.read(path)
                # Return first non-empty 3 lines as a snippet
                snippet_lines = [ln for ln in text.splitlines() if ln.strip()][:3]
                linked[path] = "\n".join(snippet_lines)
            except Exception:
                pass  # linked file doesn't exist yet — skip silently

    return linked
