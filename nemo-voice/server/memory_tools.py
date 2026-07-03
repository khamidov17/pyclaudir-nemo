"""Shared-memory voice tools — remember/recall/chat search/Telegram/time.

Moved out of voice_brain so the capability registry can own them like any
other feature module: FUNCTIONS (schemas), TOOL_NAMES, and an async
dispatch(name, args). Reads and writes the SAME memory store the
text/Telegram Nemo uses (``data/memories/`` + ``pyclaudir.db``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

import memory_index
import memory_search
import memory_store
import recall as recall_v2
import reminders
import voice_history

LOG = logging.getLogger("nemo.memory_tools")

# The real Nemo memory store, shared with the text assistant. Overridable for
# tests via NEMO_VOICE_DATA_DIR.
DATA_DIR = Path(
    os.environ.get("NEMO_VOICE_DATA_DIR")
    or (Path(__file__).resolve().parents[2] / "data")
)
MEM_DIR = DATA_DIR / "memories"
DB = DATA_DIR / "pyclaudir.db"  # the text/Telegram Nemo's conversation store
_VOICE_NOTES = MEM_DIR / "voice_notes.md"
_MEMORY_INDEX_DB = DATA_DIR / "memory_index.db"  # semantic_memory's chunk store
_TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
# No default chat id: a hardcoded fallback would silently message whoever it
# points at on a misconfigured deploy. Require the env var; send_telegram
# refuses if it's unset.
_CHAT_ID = os.environ.get("NEMO_DEFAULT_CHAT_ID", "").strip()
_UTC_OFFSET_HOURS = int(os.environ.get("NEMO_UTC_OFFSET", "5"))

FUNCTIONS: list[dict] = [
    {
        "name": "remember",
        "description": (
            "Save a short note to long-term memory shared with Avazbek's text assistant. "
            "Use when he tells you something worth keeping (a preference, fact, plan, or name)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "The fact to remember, one concise sentence.",
                }
            },
            "required": ["note"],
        },
    },
    {
        "name": "recall",
        "description": "Search saved memory for something Avazbek told you before.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look up."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_chat",
        "description": (
            "Search Avazbek's Telegram DM history with Nemo (both his messages and "
            "Nemo's replies) to recall what was discussed before. Leave query empty "
            "to get the most recent messages."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Words to find; empty for most recent.",
                }
            },
        },
    },
    {
        "name": "send_telegram",
        "description": "Send Avazbek a message on Telegram. Only when he explicitly asks you to text/message him.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The message to send."}
            },
            "required": ["text"],
        },
    },
    {
        "name": "get_time",
        "description": "Get Avazbek's current local date and time.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "enroll_speaker",
        "description": (
            "Remember someone's voice by name ('remember Aziz's voice'). After "
            "calling this, ask that person to say a full sentence — their NEXT "
            "utterance becomes their voiceprint, so future speech is attributed "
            "to them in memory. Attribution only — never gives them access."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The person's name."}
            },
            "required": ["name"],
        },
    },
]

TOOL_NAMES = {f["name"] for f in FUNCTIONS}


async def dispatch(name: str, args: dict) -> str:
    if name == "remember":
        return _remember(args.get("note", ""))
    if name == "recall":
        return _recall(args.get("query", ""))
    if name == "search_chat":
        return _search_chat(args.get("query", ""))
    if name == "send_telegram":
        return await _send_telegram(args.get("text", ""))
    if name == "get_time":
        return _get_time()
    if name == "enroll_speaker":
        # Session-scoped arming happens in the pump (it owns SpeakerState);
        # reaching here means the pump interception was bypassed somehow.
        return json.dumps({"error": "enrollment must run inside a voice session"})
    return json.dumps({"error": f"unknown function {name}"})


def _remember(note: str) -> str:
    note = (note or "").strip()
    if not note:
        return json.dumps({"error": "empty note"})
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with _VOICE_NOTES.open("a") as f:
        f.write(f"- ({ts}) {note}\n")
    # Dual-write during the v2 transition: the flat file keeps the text/Telegram
    # Nemo's shared memory intact; the v2 store powers structured recall.
    if memory_store.memory_v2_enabled():
        memory_store.add_fact(note, source="remember_tool")
    LOG.debug("voice remembered (%d chars)", len(note))
    return json.dumps({"status": "saved"})


def _recall(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return json.dumps({"results": []})
    if memory_store.memory_v2_enabled():
        rows = memory_search.search(q, limit=8)
        if rows:
            return json.dumps({"results": [r.text for r in rows]})
    # Prefer semantic recall (by meaning) when embeddings are configured — it
    # finds "I like dark roast" for "what coffee do I drink". Fall back to plain
    # keyword search if it's unavailable or finds nothing.
    if memory_index.available():
        hits = memory_index.search(q, limit=8)
        if hits:
            return json.dumps({"results": hits})
    return json.dumps({"results": _keyword_recall(q.lower())[:10]})


def _keyword_recall(q: str) -> list[str]:
    """Substring search over memory files + the verbatim voice journal."""
    hits: list[str] = []
    if MEM_DIR.is_dir():
        for f in sorted(MEM_DIR.glob("**/*.md")):
            try:
                lines = f.read_text().splitlines()
            except OSError:
                continue
            # Cap each hit so one runaway line can't bloat the tool result.
            hits.extend(ln.strip()[:200] for ln in lines if q in ln.lower())
    hits.extend(voice_history.search(q, limit=6))
    return hits


def _search_chat(query: str, limit: int = 6) -> str:
    """Search the Telegram DM history (read-only). Returns a few recent matches —
    small + on-demand so it stays cheap, never the whole transcript."""
    q = (query or "").strip()
    if not DB.exists():
        return json.dumps({"results": []})
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        cur = con.cursor()
        if q:
            rows = cur.execute(
                "SELECT direction, timestamp, text FROM messages "
                "WHERE text LIKE ? AND COALESCE(deleted,0)=0 "
                "ORDER BY timestamp DESC LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()
        else:
            rows = cur.execute(
                "SELECT direction, timestamp, text FROM messages "
                "WHERE COALESCE(deleted,0)=0 ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        con.close()
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    results = [
        {
            "who": "Avazbek" if d == "in" else "Nemo",
            "when": ts,
            "text": (t or "")[:200],
        }
        for d, ts, t in rows
    ]
    return json.dumps({"results": results})


async def _send_telegram(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return json.dumps({"error": "empty message"})
    if not _TELEGRAM_TOKEN or not _CHAT_ID:
        # App-only (no Telegram): surface it on the phone via the engine.
        return reminders.notify_now(text)
    url = f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json={"chat_id": int(_CHAT_ID), "text": text}
        ) as resp:
            ok = resp.status == 200
    return json.dumps({"status": "sent" if ok else "failed"})


def _get_time() -> str:
    now = datetime.now(timezone.utc) + timedelta(hours=_UTC_OFFSET_HOURS)
    return json.dumps(
        {"time": now.strftime("%A, %Y-%m-%d %H:%M"), "tz": f"UTC+{_UTC_OFFSET_HOURS}"}
    )


async def shared_memory_context(query: str, top_k: int = 3) -> list[str]:
    """The thinker's context feed. With VOICE_MEMORY_V2 this is budgeted
    semantic recall over the structured store; otherwise the legacy keyword
    search against memory_index.db (moved here from the orchestrator).
    """
    if memory_store.memory_v2_enabled():
        return await recall_v2.context_for(query, top_k=top_k)

    def _query() -> list[str]:
        if not _MEMORY_INDEX_DB.exists():
            return []
        try:
            words = query.lower().split()[:6]
            con = sqlite3.connect(str(_MEMORY_INDEX_DB), timeout=1.0)
            rows = con.execute(
                "SELECT text FROM chunks WHERE source='memory' "
                "ORDER BY rowid DESC LIMIT 50"
            ).fetchall()
            con.close()
            scored = [
                (sum(1 for w in words if w in t.lower()), t[:200]) for (t,) in rows
            ]
            scored = [(s, t) for s, t in scored if s > 0]
            scored.sort(key=lambda x: -x[0])
            return [t for _, t in scored[:top_k]]
        except Exception:  # noqa: BLE001 — best-effort retrieval, never crash
            return []

    return await asyncio.to_thread(_query)
