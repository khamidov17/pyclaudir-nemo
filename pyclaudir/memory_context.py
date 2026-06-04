"""Contextual memory injection — enrich turns with relevant memory snippets.

Before a message reaches the engine, this module extracts keywords from the
text, greps all memory files for matches, and returns a compact context block
the engine prepends to the turn XML. Zero API calls, zero latency overhead
beyond an in-process file scan.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .storage.memory import MemoryStore

log = logging.getLogger("pyclaudir.memory_context")

_STOPWORDS = frozenset(
    """
    a an the and or but in on at to for of with is are was were be been
    being have has had do does did will would could should may might must
    can shall i me my we our you your he she it his her its they them their
    this that these those what which who how when where why i'm you're we're
    it's that's what's don't can't won't isn't aren't wasn't weren't ok okay
    hi hey hello please thanks thank just get let know want need make sure
    also well yes no not very much more some any all
    """.split()
)

_MIN_WORD_LEN = 4
_MAX_KEYWORDS = 3
_MAX_MATCHES_PER_KEYWORD = 2
_MAX_TOTAL_MATCHES = 5
_CONTEXT_LINES = 1


def extract_keywords(text: str) -> list[str]:
    """Return up to _MAX_KEYWORDS meaningful words from *text*."""
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    seen: set[str] = set()
    keywords: list[str] = []
    for w in words:
        if w not in _STOPWORDS and w not in seen:
            seen.add(w)
            keywords.append(w)
        if len(keywords) >= _MAX_KEYWORDS:
            break
    return keywords


def build_memory_context(text: str, store: "MemoryStore") -> str | None:
    """Search memories for keywords from *text*; return compact context or None."""
    keywords = extract_keywords(text)
    if not keywords:
        return None

    seen_keys: set[str] = set()
    matches: list[dict] = []

    for kw in keywords:
        if len(matches) >= _MAX_TOTAL_MATCHES:
            break
        pattern = re.compile(re.escape(kw), re.IGNORECASE)
        for mem_file in store.list():
            if len(matches) >= _MAX_TOTAL_MATCHES:
                break
            try:
                file_text = store.read(mem_file.relative_path)
            except Exception:
                continue
            file_matches = _find_matches(mem_file.relative_path, file_text, pattern)
            for m in file_matches[:_MAX_MATCHES_PER_KEYWORD]:
                key = f"{m['file']}:{m['line']}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    matches.append(m)

    if not matches:
        return None

    log.debug("memory injection: %d snippet(s) for keywords %s", len(matches), keywords)
    lines = [f"[{m['file']}:{m['line']}] {_flatten(m['context'])}" for m in matches]
    return "\n".join(lines)


def _flatten(context: str) -> str:
    """Collapse multiline context block to a single readable line."""
    return " | ".join(
        ln.lstrip("→ ").strip()
        for ln in context.splitlines()
        if ln.strip()
    )


def _find_matches(filename: str, text: str, pattern: re.Pattern) -> list[dict]:
    lines = text.splitlines()
    results: list[dict] = []
    for i, line in enumerate(lines):
        if not pattern.search(line):
            continue
        start = max(0, i - _CONTEXT_LINES)
        end = min(len(lines), i + _CONTEXT_LINES + 1)
        ctx_parts = []
        for j in range(start, end):
            prefix = "→ " if j == i else "  "
            ctx_parts.append(f"{prefix}{j + 1}: {lines[j]}")
        results.append({"file": filename, "line": i + 1, "context": "\n".join(ctx_parts)})
    return results
