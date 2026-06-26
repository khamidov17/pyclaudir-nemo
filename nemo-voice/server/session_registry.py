"""Per-session Orchestrator registry.

A simple module-level dict keyed by voice session_id (UUID4 string).
Asyncio is single-threaded so no locks are needed; all callers run in the same
event loop. The registry is the only source of truth for live sessions — a
session_id absent here means the session is gone and chunks should be dropped.
"""

from __future__ import annotations

import logging

LOG = logging.getLogger("nemo.session_registry")

_registry: dict[str, object] = {}


def register(session_id: str, orchestrator: object) -> None:
    """Register an Orchestrator for a new voice session."""
    _registry[session_id] = orchestrator
    LOG.debug("session registered: %s (total=%d)", session_id[:8], len(_registry))


def unregister(session_id: str) -> None:
    """Remove the Orchestrator when a session ends."""
    _registry.pop(session_id, None)
    LOG.debug("session unregistered: %s (total=%d)", session_id[:8], len(_registry))


def get(session_id: str) -> object | None:
    """Return the Orchestrator for session_id, or None if the session is gone."""
    return _registry.get(session_id)


def active_count() -> int:
    return len(_registry)
