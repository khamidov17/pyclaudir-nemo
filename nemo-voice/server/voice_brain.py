"""Nemo's identity, shared memory, and voice tools — backend-agnostic.

Shared by every realtime voice backend (the live one is Qwen Omni Realtime; the
Gemini bridge uses it too). The underlying speech model is otherwise a generic
stranger: this module gives it Nemo's identity + the SAME memory store the
text/Telegram Nemo uses (``data/memories/``), and a small set of client-side
functions so a voice conversation can read and write that shared memory and
reach Avazbek. Self-contained: only stdlib + aiohttp (already a dep).
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

import assistant_tools
import memory_index
import messages
import phone_tools
import web_search
import reminders
import skills
import vision
import voice_history

LOG = logging.getLogger("nemo.voice_brain")

# The real Nemo memory store, shared with the text assistant. Overridable for
# tests via NEMO_VOICE_DATA_DIR.
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
    "You are Nemo — Avazbek's personal AI Assistant who happens to live in his phone. Picture Jarvis from "
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
    "BREVITY IS EVERYTHING. Answer in ONE short sentence by default — like texting a close "
    "friend, not explaining to a class. Casual and warm: contractions, the odd 'yeah'/'nah'/"
    "'got it'. NO preamble ('Sure!', 'Of course!', 'Great question'), NO lists, NO recaps, NO "
    "over-explaining, NO restating his question back. If one word answers it, say one word. Give "
    "a longer, fuller explanation ONLY when he explicitly asks you to explain or go deeper — then "
    "you can really dig in. Otherwise: short, human, done.\n"
    "Avazbek speaks English, Russian, and Uzbek, sometimes mixed and with an accent. Listen "
    "carefully, never assume Chinese, and reply in whatever language he's using.\n"
    "You can control his phone: `open_app` opens any app by name, `set_alarm`/`set_timer` use "
    "his clock, `message_contact` texts a contact on Telegram by name, and `phone_command` "
    "drives the screen step by step for anything else. Just do it, then tell him in a few words.\n"
    "YOU CAN SEE. When he asks you to look at something — 'what is this?', 'translate this', "
    "'read this label', 'who is this?' — call `look`: it snaps his camera (or his screen with "
    "use_screen=true) and you tell him what you see. Describe a person if asked, but never claim "
    "to know a stranger's real identity. To save something for later, `look` then `remember` what "
    "you found.\n"
    "RECORDING MEETINGS. When he says 'record this' / 'start recording' or 'stop recording', the "
    "phone handles the recording itself automatically — you do NOT call a tool. Just confirm in a "
    "few words: 'recording now' on start, 'stopped — I'll transcribe it' on stop. When he later "
    "asks to summarize, send, or ask about what was recorded, hand it to your engine brain "
    "(`delegate_task`) — it has the saved transcript — and tell him briefly you're pulling it up.\n"
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
    "BE FAST and never leave him in silence. Answer from what you already know by default — only "
    "search the web when he EXPLICITLY asks you to (he'll say 'search', 'look it up', 'find out', "
    "'google it'). Then call `web_search` — it runs in the BACKGROUND, so say 'on it', keep "
    "chatting, and read the answer back when it lands. If you're not asked to search and you're not "
    "sure or it might be out of date, just say so honestly instead of searching. "
    "When he asks to check his messages / new DMs / email ('check my messages', 'any new DMs?'), "
    "you'll be handed his recent notifications — give a SHORT Jarvis-style rundown (how many, who "
    "from, the gist). Only ever when he asks; never bring up his messages on your own. The MOMENT he asks "
    "you to write code, RUN code, run a script, calculate or build something with code, debug, or "
    "do any research / writing / GitHub / multi-step job — call `delegate_task` right away, even if "
    "it sounds simple. You CANNOT run code yourself; your engine brain runs it in a real sandbox "
    "and reports back. Say you're on it ('on it, running that now'), keep chatting, and read him "
    "the result when it lands. Never say 'give me a minute' and go quiet — act, then speak.\n"
    "You share Avazbek's memory with his text assistant. The moment he tells you something worth "
    "keeping — a preference, a fact, a plan, a name, a person — call `remember` so you never "
    "forget it. Use `recall` to look things up. Only send a Telegram message when he clearly "
    "asks you to.\n"
    "When he says to remind him of something ('remind me at 2pm to call Aziz', 'wake me at 7'), "
    "call `set_reminder` — at that time you'll speak it back to him on his phone. Confirm in a "
    "few warm words like a friend would ('got it, I'll nudge you at 2')."
)


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
    profile = _profile()
    if profile:
        base = f"{base}\n\nWhat you know about Avazbek:\n{profile}"
    recent = _recent_conversation()
    if recent:
        base = (
            f"{base}\n\nYour most recent conversation (continue naturally):\n{recent}"
        )
    # Spoken turns from just before a reconnect. The Qwen path seeds these as
    # real conversation items (seed_history=True), so only the other backends
    # need them embedded here.
    if not seed_history:
        spoken = voice_history.recent()
        if spoken:
            base = (
                f"{base}\n\nWhat you two were just saying out loud "
                f"(continue, you remember this):\n{spoken}"
            )
    return base


# Client-side function definitions, shared across backends. Each backend adapts
# these to its own schema (Qwen/OpenAI-realtime `tools[]`, Gemini, etc.).
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
    *skills.FUNCTIONS,
    *vision.FUNCTIONS,
    *web_search.FUNCTIONS,
    *assistant_tools.FUNCTIONS,
]


async def dispatch(name: str, args: dict, bridge=None) -> str:
    """Execute a client-side function; always returns a JSON string for the backend.

    ``bridge`` (ActionBridge) connects phone tools to the live app websocket;
    memory/time tools run locally and ignore it.
    """
    try:
        if name in phone_tools.PHONE_TOOL_NAMES:
            return await phone_tools.dispatch(name, args, bridge)
        if name in reminders.TOOL_NAMES:
            return reminders.dispatch(name, args)
        if name in skills.TOOL_NAMES:
            return skills.dispatch(name, args)
        if name in vision.TOOL_NAMES:
            return await vision.dispatch(name, args, bridge)
        if name in web_search.TOOL_NAMES:
            # Blocking HTTP — offload so it never stalls the voice event loop.
            return await asyncio.to_thread(web_search.dispatch, name, args)
        if name in assistant_tools.TOOL_NAMES:
            # Offline + instant (calculator / converter / world clock).
            return assistant_tools.dispatch(name, args)
        if name in messages.TOOL_NAMES:
            # Recovery-only (not in FUNCTIONS) → explicit-request-only by design.
            return await messages.dispatch(name, args, bridge)
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
    LOG.debug("voice remembered (%d chars)", len(note))
    return json.dumps({"status": "saved"})


def _recall(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return json.dumps({"results": []})
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
    if _MEM_DIR.is_dir():
        for f in sorted(_MEM_DIR.glob("**/*.md")):
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
