"""Nemo's identity and prompt/tool composition — backend-agnostic.

Shared by every realtime voice backend (the live one is Qwen Omni Realtime; the
Gemini bridge uses it too). The underlying speech model is otherwise a generic
stranger: this module gives it Nemo's identity, composed from the capability
registry (capabilities.py), plus the SAME memory store the text/Telegram Nemo
uses (``data/memories/``). Feature prose, tool schemas, and dispatchers all
live in the registry — adding a feature means one Capability entry there, not
edits here.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import capabilities
import memory_store
import persona
import recall
import voice_history
from memory_tools import DB as _DB
from memory_tools import MEM_DIR as _MEM_DIR

LOG = logging.getLogger("nemo.voice_brain")

_MAX_MEMORY_CHARS = 4000

_IDENTITY = persona.prompt() + capabilities.identity_fragments()


def _read_memory_digest() -> str:
    """Concatenate the shared memory files into a compact digest for the prompt.

    Whole files are included up to a character budget. If some don't fit, a
    short marker tells the model the rest is reachable via ``recall`` — so a
    growing memory degrades gracefully instead of being silently truncated
    mid-sentence (which used to drop later files entirely and could cut a file
    off mid-fact)."""
    if not _MEM_DIR.is_dir():
        return ""
    parts: list[str] = []
    used = 0
    skipped = 0
    for f in sorted(_MEM_DIR.glob("**/*.md")):
        try:
            body = f.read_text().strip()
        except OSError:
            continue
        if not body:
            continue
        block = f"## {f.name}\n{body}"
        if parts and used + len(block) + 2 > _MAX_MEMORY_CHARS:
            skipped += 1  # would overflow — keep whole-file granularity
            continue
        if not parts and len(block) > _MAX_MEMORY_CHARS:
            block = block[:_MAX_MEMORY_CHARS]  # one giant file: include its head
        parts.append(block)
        used += len(block) + 2  # +2 for the "\n\n" join separator
    digest = "\n\n".join(parts).strip()
    if skipped:
        digest += f"\n\n(+{skipped} more memory file(s) not shown — use `recall`.)"
    return digest


def _profile() -> str:
    """The 'who is Avazbek' digest for the prompt: his consolidated profile if
    the text assistant has synthesized one, otherwise every saved memory file.
    This is the spine of Nemo's memory — sent once per session, so it can be
    generous without per-turn cost."""
    about = _MEM_DIR / "ABOUT_ME.md"  # the text Nemo's consolidated profile
    if about.is_file():
        try:
            return about.read_text().strip()[:_MAX_MEMORY_CHARS]
        except OSError:
            pass
    return _read_memory_digest()


def _recent_conversation(limit: int = 8) -> str:
    """The last few things Avazbek said to Nemo (Telegram + voice), so a new
    voice session continues where the last conversation left off instead of
    starting cold. Read-only, newest last."""
    if not _DB.exists():
        return ""
    try:
        con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT direction, text FROM messages "
            "WHERE COALESCE(deleted,0)=0 AND text IS NOT NULL AND text != '' "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        con.close()
    except Exception:
        return ""
    lines = [
        f"{'Avazbek' if d == 'in' else 'Nemo'}: {(t or '').strip()[:160]}"
        for d, t in reversed(rows)
    ]
    return "\n".join(lines).strip()


def build_prompt(seed_history: bool = False) -> str:
    """The voice agent's system prompt: Nemo identity + what he knows about
    Avazbek + how their last conversation went. Deeper lookups still go through
    tools (recall / search_chat), but the spine of his memory rides in the
    prompt so he never sounds like a stranger.

    ``seed_history=True`` (the Qwen path) means the recent SPOKEN turns are
    seeded as real conversation items instead, so they're omitted here to avoid
    showing them twice.
    """
    base = (
        f"{_IDENTITY}\n\nYou genuinely remember Avazbek across every conversation. "
        "Use your tools to go deeper: `recall` for saved facts, `search_chat` for "
        "his full Telegram history with you, `get_time` for the time. For exact "
        "numbers, always reach for a tool instead of guessing: `calculate` for any "
        "arithmetic (percentages, tips, square roots, big sums), `convert_units` for "
        "unit conversions (miles↔km, lbs↔kg, °F↔°C), and `world_time` for the time in "
        "another city — these are instant and offline, so use them, don't delegate "
        "simple math. The moment he "
        "tells you anything worth keeping — a preference, fact, plan, name, or person "
        "— call `remember` immediately so it's there next time.\n"
        "CONTINUITY: you are almost always picking up an ONGOING conversation — a "
        "brief pause or a network reconnection, not a fresh start. Never greet, say "
        "'hi/hello', or re-introduce yourself mid-conversation; just continue the "
        "thread naturally, exactly where it left off, as if nothing happened. Only a "
        "real first hello belongs at the genuine start of a brand-new chat."
    )
    return base + _memory_blocks(seed_history)


def _memory_blocks(seed_history: bool) -> str:
    """Profile + v2 memory + recent-conversation sections of the prompt."""
    parts: list[str] = []
    profile = _profile()
    if profile:
        parts.append(f"What you know about Avazbek:\n{profile}")
    if memory_store.memory_v2_enabled():
        v2 = recall.profile_block()
        if v2:
            parts.append(v2)
    recent = _recent_conversation()
    if recent:
        parts.append(f"Your most recent conversation (continue naturally):\n{recent}")
    # Spoken turns from just before a reconnect. The Qwen path seeds these as
    # real conversation items (seed_history=True), so only the other backends
    # need them embedded here.
    if not seed_history:
        spoken = voice_history.recent()
        if spoken:
            parts.append(
                "What you two were just saying out loud "
                f"(continue, you remember this):\n{spoken}"
            )
    return "".join(f"\n\n{p}" for p in parts)


# Client-side function definitions, shared across backends. Each backend adapts
# these to its own schema (Qwen/OpenAI-realtime `tools[]`, Gemini, etc.).
FUNCTIONS: list[dict] = capabilities.all_functions()


async def dispatch(name: str, args: dict, bridge=None) -> str:
    """Execute a client-side function; always returns a JSON string for the backend.

    ``bridge`` (ActionBridge) connects phone tools to the live app websocket;
    memory/time tools run locally and ignore it.
    """
    try:
        result = await capabilities.dispatch(name, args, bridge)
        if result is not None:
            return result
        return json.dumps({"error": f"unknown function {name}"})
    except Exception as exc:  # never let a tool error kill the turn
        LOG.exception("voice function %s failed", name)
        return json.dumps({"error": str(exc)})
