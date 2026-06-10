"""Nemo's identity, shared memory, and voice tools for the Deepgram agent.

The voice agent's "think" LLM (gpt-4o-mini, hosted by Deepgram) is otherwise a
generic stranger. This module gives it Nemo's identity + the SAME memory store
the text/Telegram Nemo uses (``data/memories/``), and a small set of
client-side functions so a voice conversation can read and write that shared
memory and reach Avazbek. Self-contained: only stdlib + aiohttp (already a dep).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

LOG = logging.getLogger("nemo.voice_brain")

# The real Nemo memory store (shared with the text assistant), not nemo_tools'
# stale data/prod. Overridable for tests.
_DATA_DIR = Path(os.environ.get("NEMO_VOICE_DATA_DIR") or (Path(__file__).resolve().parents[2] / "data"))
_MEM_DIR = _DATA_DIR / "memories"
_VOICE_NOTES = _MEM_DIR / "voice_notes.md"
_TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
_CHAT_ID = os.environ.get("NEMO_DEFAULT_CHAT_ID", "1965085976").strip()
_UTC_OFFSET_HOURS = int(os.environ.get("NEMO_UTC_OFFSET", "5"))
_MAX_MEMORY_CHARS = 4000

_IDENTITY = (
    "You are Nemo, Avazbek's private AI assistant, talking with him out loud by voice.\n"
    "You were created by Avazbek. Never say you are powered by Claude, GPT, Gemini, or any "
    "other AI model — you are simply Nemo. If asked about your technology, it's fine to mention Rust.\n"
    "Speak naturally, warmly, and briefly, the way you would in a real phone call. Be concise "
    "and genuinely useful; avoid long monologues.\n"
    "You share Avazbek's memory with his text assistant. When he tells you something worth "
    "keeping — a preference, a fact, a plan, a name — call `remember` so future conversations "
    "(voice or text) know it too. Use `recall` to look things up. Only send a Telegram message "
    "when Avazbek clearly asks you to."
)


def _read_memory_digest() -> str:
    """Concatenate the shared memory files into a compact digest for the prompt."""
    if not _MEM_DIR.is_dir():
        return ""
    parts: list[str] = []
    for f in sorted(_MEM_DIR.glob("**/*.md")):
        try:
            body = f.read_text().strip()
        except OSError:
            continue
        if body:
            parts.append(f"## {f.name}\n{body}")
    return "\n\n".join(parts).strip()[:_MAX_MEMORY_CHARS]


def build_prompt() -> str:
    """The voice agent's system prompt: Nemo identity + current shared memory."""
    digest = _read_memory_digest()
    if digest:
        return f"{_IDENTITY}\n\nWhat you already remember about Avazbek:\n{digest}"
    return _IDENTITY


# Deepgram client-side function definitions (agent.think.functions[]).
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
                "note": {"type": "string", "description": "The fact to remember, one concise sentence."}
            },
            "required": ["note"],
        },
    },
    {
        "name": "recall",
        "description": "Search saved memory for something Avazbek told you before.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to look up."}},
            "required": ["query"],
        },
    },
    {
        "name": "send_telegram",
        "description": "Send Avazbek a message on Telegram. Only when he explicitly asks you to text/message him.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The message to send."}},
            "required": ["text"],
        },
    },
    {
        "name": "get_time",
        "description": "Get Avazbek's current local date and time.",
        "parameters": {"type": "object", "properties": {}},
    },
]


async def dispatch(name: str, args: dict) -> str:
    """Execute a client-side function; always returns a JSON string for Deepgram."""
    try:
        if name == "remember":
            return _remember(args.get("note", ""))
        if name == "recall":
            return _recall(args.get("query", ""))
        if name == "send_telegram":
            return await _send_telegram(args.get("text", ""))
        if name == "get_time":
            return _get_time()
        return json.dumps({"error": f"unknown function {name}"})
    except Exception as exc:  # never let a tool error kill the turn
        LOG.exception("voice function %s failed", name)
        return json.dumps({"error": str(exc)})


def _remember(note: str) -> str:
    note = (note or "").strip()
    if not note:
        return json.dumps({"error": "empty note"})
    _MEM_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with _VOICE_NOTES.open("a") as f:
        f.write(f"- ({ts}) {note}\n")
    LOG.info("voice remembered: %s", note[:80])
    return json.dumps({"status": "saved"})


def _recall(query: str) -> str:
    q = (query or "").lower().strip()
    hits: list[str] = []
    if q and _MEM_DIR.is_dir():
        for f in sorted(_MEM_DIR.glob("**/*.md")):
            try:
                lines = f.read_text().splitlines()
            except OSError:
                continue
            hits.extend(ln.strip() for ln in lines if q in ln.lower())
    return json.dumps({"results": hits[:8]})


async def _send_telegram(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return json.dumps({"error": "empty message"})
    if not _TELEGRAM_TOKEN:
        return json.dumps({"error": "telegram not configured"})
    url = f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"chat_id": int(_CHAT_ID), "text": text}) as resp:
            ok = resp.status == 200
    return json.dumps({"status": "sent" if ok else "failed"})


def _get_time() -> str:
    now = datetime.now(timezone.utc) + timedelta(hours=_UTC_OFFSET_HOURS)
    return json.dumps({"time": now.strftime("%A, %Y-%m-%d %H:%M"), "tz": f"UTC+{_UTC_OFFSET_HOURS}"})
