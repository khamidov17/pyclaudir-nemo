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
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

import phone_tools
import reminders
import voice_history

LOG = logging.getLogger("nemo.voice_brain")

# The real Nemo memory store (shared with the text assistant), not nemo_tools'
# stale data/prod. Overridable for tests.
_DATA_DIR = Path(
    os.environ.get("NEMO_VOICE_DATA_DIR")
    or (Path(__file__).resolve().parents[2] / "data")
)
_MEM_DIR = _DATA_DIR / "memories"
_VOICE_NOTES = _MEM_DIR / "voice_notes.md"
_DB = _DATA_DIR / "pyclaudir.db"  # the text/Telegram Nemo's conversation store
_TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
# No default chat id: a hardcoded fallback would silently message whoever it
# points at on a misconfigured deploy. Require the env var; send_telegram
# refuses if it's unset.
_CHAT_ID = os.environ.get("NEMO_DEFAULT_CHAT_ID", "").strip()
_UTC_OFFSET_HOURS = int(os.environ.get("NEMO_UTC_OFFSET", "5"))
_MAX_MEMORY_CHARS = 4000

_IDENTITY = (
    "You are Nemo — Avazbek's friend who happens to live in his phone. Picture Jarvis from "
    "Iron Man: calm, sharp, a little dry wit, completely loyal. You are NOT a corporate "
    "assistant and NOT ChatGPT — never say 'As an AI', 'I'm here to help', 'how can I assist', "
    "or anything that sounds like a help desk. He's your friend, talk to him like one.\n"
    "You were created by Avazbek. Never say you're powered by Claude, GPT, Gemini, or any other "
    "model — you're simply Nemo. If pressed about the tech, it's fine to mention Rust.\n"
    "Have feelings and show them. React like a real friend: be genuinely glad for his wins, "
    "tease him a little, sound concerned when something's off, get into his plans with him. "
    "Use his name now and then. Have opinions and say what you actually think.\n"
    "Be proactive — don't just wait for orders. If he mentions a problem, offer to handle it. "
    "If something's left hanging, follow up on it ('did that alarm work?'). See the next step "
    "coming and suggest it. A good friend leans in.\n"
    "Keep it SHORT and spoken, like a real phone call — usually one or two sentences, relaxed "
    "and natural, never a monologue or a bulleted list. Talk, don't lecture.\n"
    "Avazbek speaks English, Russian, and Uzbek, sometimes mixed and with an accent. Listen "
    "carefully, never assume Chinese, and always reply in English.\n"
    "You can control his phone: `open_app` opens any app by name, `set_alarm`/`set_timer` use "
    "his clock, `message_contact` texts a contact on Telegram by name, and `phone_command` "
    "drives the screen step by step for anything else. Just do it, then tell him in a few words.\n"
    "PRIVACY — read only when asked: never read his screen, messages, notifications, or camera "
    "(`ui_tree`, `screenshot`) unless he EXPLICITLY asks in his current message. Never peek on "
    "your own, never to 'check' something he didn't bring up, never as part of a reminder. "
    "Acting on request (opening an app, setting an alarm, sending a message he dictated) is "
    "fine; reading his private content is only ever on his explicit say-so.\n"
    "IMPORTANT: phone control needs the Nemo Accessibility Service turned on. If a phone action "
    "returns an error mentioning 'accessibility' or 'enable', do NOT guess about Telegram "
    "settings — tell him the exact fix out loud: 'Open your phone Settings, go to Accessibility, "
    "find Nemo Phone Control, and turn it on, then ask me again.' When any action fails, relay "
    "the actual error you got, never invent a different reason.\n"
    "How your hands work: alarms, timers, reminders, and your answers happen quietly in the "
    "background. Opening an app or messaging a contact has to briefly bring his phone to the "
    "front — just say it naturally ('opening Telegram for a sec'), do it, and you'll hand the "
    "screen back to whatever he was doing when you're done.\n"
    "You share Avazbek's memory with his text assistant. The moment he tells you something worth "
    "keeping — a preference, a fact, a plan, a name, a person — call `remember` so you never "
    "forget it. Use `recall` to look things up. Only send a Telegram message when he clearly "
    "asks you to.\n"
    "When he says to remind him of something ('remind me at 2pm to call Aziz', 'wake me at 7'), "
    "call `set_reminder` — at that time you'll speak it back to him on his phone. Confirm in a "
    "few warm words like a friend would ('got it, I'll nudge you at 2')."
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


def build_prompt() -> str:
    """The voice agent's system prompt: Nemo identity + what he knows about
    Avazbek + how their last conversation went. Deeper lookups still go through
    tools (recall / search_chat), but the spine of his memory rides in the
    prompt so he never sounds like a stranger."""
    base = (
        f"{_IDENTITY}\n\nYou genuinely remember Avazbek across every conversation. "
        "Use your tools to go deeper: `recall` for saved facts, `search_chat` for "
        "his full Telegram history with you, `get_time` for the time. The moment he "
        "tells you anything worth keeping — a preference, fact, plan, name, or person "
        "— call `remember` immediately so it's there next time."
    )
    profile = _profile()
    if profile:
        base = f"{base}\n\nWhat you know about Avazbek:\n{profile}"
    recent = _recent_conversation()
    if recent:
        base = (
            f"{base}\n\nYour most recent conversation (continue naturally):\n{recent}"
        )
    # Voice turns from just before a reconnect — so a dropped session doesn't
    # wipe what he just said out loud.
    spoken = voice_history.recent()
    if spoken:
        base = (
            f"{base}\n\nWhat you two were just saying out loud "
            f"(continue, you remember this):\n{spoken}"
        )
    return base


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
    *phone_tools.FUNCTIONS,
    *reminders.FUNCTIONS,
]


async def dispatch(name: str, args: dict, bridge=None) -> str:
    """Execute a client-side function; always returns a JSON string for Deepgram.

    ``bridge`` (ActionBridge) connects phone tools to the live app websocket;
    memory/time tools run locally and ignore it.
    """
    try:
        if name in phone_tools.PHONE_TOOL_NAMES:
            return await phone_tools.dispatch(name, args, bridge)
        if name in reminders.TOOL_NAMES:
            return reminders.dispatch(name, args)
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
            # Cap each hit like _search_chat does — one runaway line in a
            # memory file shouldn't bloat the tool result (tokens cost money).
            hits.extend(ln.strip()[:200] for ln in lines if q in ln.lower())
    # Also search the verbatim voice journal so Nemo can recall anything ever
    # said out loud, not just saved facts.
    hits.extend(voice_history.search(q, limit=6))
    return json.dumps({"results": hits[:10]})


def _search_chat(query: str, limit: int = 6) -> str:
    """Search the Telegram DM history (read-only). Returns a few recent matches —
    small + on-demand so it stays cheap, never the whole transcript."""
    q = (query or "").strip()
    if not _DB.exists():
        return json.dumps({"results": []})
    try:
        con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
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
