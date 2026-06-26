"""StateSnapshot — the shared-context bus between voice (Tier-0) and engine (Tier-2).

One instance lives per voice session, owned exclusively by the Orchestrator.
Single-writer (Orchestrator serializes all writes) → no locks needed.
`rev` is monotonically incremented on every meaningful update so stale engine
chunks (rev < snapshot.rev) can be detected and dropped.

The snapshot is in-memory only — it is NEVER persisted or journaled.
Sensitive content (message summaries) never enters entities/plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_INJECT_RE = re.compile(r"(<[^>]{0,80}>|\[INST\]|###\s*\w)")
_MAX_ENTITY_LEN = 200


def _sanitize_value(v: Any) -> Any:
    """Cap entity string values and strip prompt-injection patterns."""
    if not isinstance(v, str):
        return v
    v = v[:_MAX_ENTITY_LEN]
    v = _INJECT_RE.sub("", v)
    return v


@dataclass
class StateSnapshot:
    session_id: str
    corrected_text: str = ""
    entities: dict[str, Any] = field(default_factory=dict)
    plan: list[str] = field(default_factory=list)
    retrieved_memories: list[str] = field(default_factory=list)
    route: str = "tier0"
    rev: int = 0

    def bump(self) -> None:
        """Increment rev — call after any meaningful state change."""
        self.rev += 1

    def reset_turn(self) -> None:
        """Clear per-turn working state on barge-in or new topic (rev bumped)."""
        self.corrected_text = ""
        self.entities = {}
        self.plan = []
        self.retrieved_memories = []
        self.bump()

    def set_transcript(self, text: str) -> None:
        self.corrected_text = text[:4000]
        self.bump()

    def set_route(self, route: str) -> None:
        self.route = route
        self.bump()

    def add_entity(self, key: str, value: Any) -> None:
        self.entities[key[:80]] = _sanitize_value(value)

    def to_slice(self) -> dict[str, Any]:
        """Compact dict sent to the engine alongside the task text."""
        return {
            "corrected_text": self.corrected_text,
            "entities": self.entities,
            "plan": self.plan[:10],
            "retrieved_memories": self.retrieved_memories[:5],
            "route": self.route,
            "rev": self.rev,
        }
