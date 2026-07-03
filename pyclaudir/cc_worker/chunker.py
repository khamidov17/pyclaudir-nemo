"""ClauseChunker — splits CC streaming text into speakable clause-sized chunks.

Fires on sentence-ending punctuation (. ! ?) or on a comma that follows ≥8
non-whitespace chars, both when immediately followed by whitespace (sentence
boundary signal). The caller feeds successive text-block strings from the CC
worker and collects ready chunks; at turn end it calls flush() for anything
remaining in the buffer.

This is pure string manipulation — no I/O, no state beyond the buffer.
"""

from __future__ import annotations

import re

# Sentence-end: .  !  ?  followed by a whitespace character.
# Clause-comma: ,  after ≥8 non-space chars, followed by a whitespace character.
# The space is consumed by the split so it's not double-emitted.
_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s|(?<=\S{8},)\s")

_MIN_CHUNK_CHARS = 4


class ClauseChunker:
    """Stateful boundary splitter. One instance per CC turn / on_chunk stream."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        """Append text to the buffer; return all complete clauses found so far."""
        self._buf += text
        parts = _BOUNDARY_RE.split(self._buf)
        if len(parts) <= 1:
            return []
        # Last part is the unfinished tail; keep it in buffer.
        self._buf = parts[-1]
        return [
            p.strip()
            for p in parts[:-1]
            if p.strip() and len(p.strip()) >= _MIN_CHUNK_CHARS
        ]

    def flush(self) -> list[str]:
        """Return whatever is left in the buffer and reset. Call at turn end."""
        tail = self._buf.strip()
        self._buf = ""
        return [tail] if tail and len(tail) >= _MIN_CHUNK_CHARS else []
