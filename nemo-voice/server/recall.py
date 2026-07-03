"""Per-turn memory recall + session-start profile for memory v2.

Two feeds into the realtime session:

* ``context_for(transcript)`` — semantic recall on what Avazbek just said,
  injected as a context item before Qwen responds. Hard time budget: past
  ``budget_ms`` we answer without memory rather than delay the turn.
* ``profile_block()`` — top live facts + open follow-ups baked into the
  session-start system prompt.

Enabled via VOICE_MEMORY_V2=1 (checked by the call sites, not here).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

import memory_search
import memory_store

LOG = logging.getLogger("nemo.recall")

# Memory text is woven into the system prompt — strip chat-template injection
# markers (same threat model as reminders.py's sanitizer) before it rides in.
_INJECT_RE = re.compile(
    r"\[/?INST\]|</s>|<s>|\[/?SYS\]|<\|[^|]{0,30}\|>", re.IGNORECASE
)


def _sanitize(text: str) -> str:
    prev = text
    while True:
        cur = _INJECT_RE.sub("", prev)
        if cur == prev:
            return cur
        prev = cur


_BUDGET_MS = int(os.environ.get("VOICE_RECALL_BUDGET_MS", "150"))
_PROFILE_FACTS = int(os.environ.get("VOICE_PROFILE_FACTS", "30"))
_PROFILE_CHARS = 3000


async def context_for(
    transcript: str, top_k: int = 4, budget_ms: int | None = None
) -> list[str]:
    """Relevant memories for this utterance, or [] if over budget/empty."""
    q = (transcript or "").strip()
    if not q:
        return []
    budget = (budget_ms if budget_ms is not None else _BUDGET_MS) / 1000
    try:
        rows = await asyncio.wait_for(
            asyncio.to_thread(memory_search.search, q, limit=top_k),
            timeout=budget,
        )
    except asyncio.TimeoutError:
        LOG.debug("recall over %.0fms budget — answering without memory", budget * 1000)
        return []
    except Exception as exc:  # noqa: BLE001 — recall must never break a turn
        LOG.warning("recall failed: %s", exc)
        return []
    return [_tag(r.kind, r.text) for r in rows]


def _tag(kind: str, text: str) -> str:
    label = {"fact": "known", "procedure": "how-to", "episode": "earlier"}[kind]
    return f"[{label}] {_sanitize(text)[:300]}"


def profile_block() -> str:
    """Prompt block of top facts + open follow-ups. '' when the store is empty."""
    try:
        facts = memory_store.live_facts(limit=_PROFILE_FACTS)
        followups = memory_store.open_followups(limit=8)
    except Exception as exc:  # noqa: BLE001 — a broken DB must not kill startup
        LOG.warning("profile_block failed: %s", exc)
        return ""
    parts: list[str] = []
    if facts:
        lines = "\n".join(f"- {_sanitize(t)}" for _, _, t in facts)
        parts.append(f"Facts you know about Avazbek:\n{lines}")
    if followups:
        lines = "\n".join(
            f"- {_sanitize(t)}" + (f" (due {d})" if d else "") for _, t, d in followups
        )
        parts.append(f"Open follow-ups you are tracking:\n{lines}")
    return "\n\n".join(parts)[:_PROFILE_CHARS]
