"""Capability registry — one descriptor per voice feature.

Adding a feature used to mean editing three places: the _IDENTITY prose, the
FUNCTIONS splat, and the dispatch() ladder in voice_brain. Now a feature is ONE
Capability entry here: its prompt prose, tool schemas, dispatcher, and
optionally TIER2 routing patterns (consulted by semantic_router) and an M2
context fetcher (consulted by the orchestrator's thinker during VAD silence).
voice_brain composes the session prompt and tool list from this registry.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import assistant_tools
import memory_tools
import messages
import navigation
import phone_tools
import reminders
import skills
import vision
import web_search

Dispatcher = Callable[[str, dict, object], Awaitable[str]]
ContextFetcher = Callable[[str], Awaitable[list[str]]]


@dataclass(frozen=True)
class Capability:
    """One voice feature: its prose, tools, dispatcher, and routing hints."""

    id: str
    prompt_fragment: str = ""
    functions: tuple[dict, ...] = ()
    tool_names: frozenset[str] = frozenset()
    dispatch: Dispatcher | None = None
    exposed: bool = True  # False → dispatch-only, never in the session tool list
    tier2_patterns: tuple[str, ...] = ()  # utterance regexes routed to the engine
    context_fetcher: ContextFetcher | None = None  # M2 thinker context source


# The wrappers below resolve module.dispatch at CALL time (not import time) so
# monkeypatching a feature module's dispatch keeps working, as it did when
# voice_brain called the modules directly.


def _sync(module: object) -> Dispatcher:
    async def run(name: str, args: dict, bridge: object) -> str:
        return module.dispatch(name, args)  # type: ignore[attr-defined]

    return run


def _threaded(module: object) -> Dispatcher:
    """Blocking I/O — offload so it never stalls the voice event loop."""

    async def run(name: str, args: dict, bridge: object) -> str:
        return await asyncio.to_thread(module.dispatch, name, args)  # type: ignore[attr-defined]

    return run


def _async_only(module: object) -> Dispatcher:
    async def run(name: str, args: dict, bridge: object) -> str:
        return await module.dispatch(name, args)  # type: ignore[attr-defined]

    return run


def _bridged(module: object) -> Dispatcher:
    async def run(name: str, args: dict, bridge: object) -> str:
        return await module.dispatch(name, args, bridge)  # type: ignore[attr-defined]

    return run


_PHONE = (
    "You can control his phone: `open_app` opens any app by name, `set_alarm`/`set_timer` use "
    "his clock, `message_contact` texts a contact on Telegram by name, and `phone_command` "
    "drives the screen step by step for anything else. Just do it, then tell him in a few words.\n"
)
_VISION = (
    "YOU CAN SEE. When he asks you to look at something — 'what is this?', 'translate this', "
    "'read this label', 'who is this?' — call `look`: it snaps his camera (or his screen with "
    "use_screen=true) and you tell him what you see. Describe a person if asked, but never claim "
    "to know a stranger's real identity. To save something for later, `look` then `remember` what "
    "you found.\n"
)
_NAVIGATION = (
    "NAVIGATION. When he asks you to direct/navigate/guide him somewhere, call "
    "`start_navigation` with the destination — his phone streams GPS and you'll receive "
    "guidance lines to speak ('in 500 meters, turn right…'); say them naturally in the "
    "conversation's language the moment they arrive. `stop_navigation` when he says stop. "
    "You can still chat normally while guiding.\n"
)
_RECORDING = (
    "RECORDING MEETINGS. When he says 'record this' / 'start recording' or 'stop recording', the "
    "phone handles the recording itself automatically — you do NOT call a tool. Just confirm in a "
    "few words: 'recording now' on start, 'stopped — I'll transcribe it' on stop. When he later "
    "asks to summarize, send, or ask about what was recorded, hand it to your engine brain "
    "(`delegate_task`) — it has the saved transcript — and tell him briefly you're pulling it up.\n"
)
_PRIVACY = (
    "PRIVACY — read only when asked: never read his screen, messages, notifications, or camera "
    "(`ui_tree`, `screenshot`) unless he EXPLICITLY asks in his current message. Never peek on "
    "your own, never to 'check' something he didn't bring up, never as part of a reminder. "
    "Acting on request (opening an app, setting an alarm, sending a message he dictated) is "
    "fine; reading his private content is only ever on his explicit say-so.\n"
)
_PHONE_NOTES = (
    "IMPORTANT: phone control needs the Nemo Accessibility Service turned on. If a phone action "
    "returns an error mentioning 'accessibility' or 'enable', do NOT guess about Telegram "
    "settings — tell him the exact fix out loud: 'Open your phone Settings, go to Accessibility, "
    "find Nemo Phone Control, and turn it on, then ask me again.' When any action fails, relay "
    "the actual error you got, never invent a different reason.\n"
    "How your hands work: alarms, timers, reminders, and your answers happen quietly in the "
    "background. Opening an app or messaging a contact has to briefly bring his phone to the "
    "front — just say it naturally ('opening Telegram for a sec'), do it, and you'll hand the "
    "screen back to whatever he was doing when you're done.\n"
)
_RESPONSIVENESS = (
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
)
_MEMORY = (
    "You share Avazbek's memory with his text assistant. The moment he tells you something worth "
    "keeping — a preference, a fact, a plan, a name, a person — call `remember` so you never "
    "forget it. Use `recall` to look things up. Only send a Telegram message when he clearly "
    "asks you to.\n"
)
_REMINDERS = (
    "When he says to remind him of something ('remind me at 2pm to call Aziz', 'wake me at 7'), "
    "call `set_reminder` — at that time you'll speak it back to him on his phone. Confirm in a "
    "few warm words like a friend would ('got it, I'll nudge you at 2')."
)

# Registry order defines prompt-prose order — keep it stable.
REGISTRY: tuple[Capability, ...] = (
    Capability(
        id="phone",
        prompt_fragment=_PHONE,
        functions=tuple(phone_tools.FUNCTIONS),
        tool_names=frozenset(phone_tools.PHONE_TOOL_NAMES),
        dispatch=_bridged(phone_tools),
    ),
    Capability(
        id="vision",
        prompt_fragment=_VISION,
        functions=tuple(vision.FUNCTIONS),
        tool_names=frozenset(vision.TOOL_NAMES),
        dispatch=_bridged(vision),
    ),
    Capability(id="recording", prompt_fragment=_RECORDING),
    Capability(id="privacy", prompt_fragment=_PRIVACY),
    Capability(id="phone_notes", prompt_fragment=_PHONE_NOTES),
    Capability(
        # Before web_search: the CLI skill named `web_search` (SKILL.md) shadows
        # the module tool, matching the old dispatch-ladder precedence.
        id="skills",
        functions=tuple(skills.FUNCTIONS),
        tool_names=frozenset(skills.TOOL_NAMES),
        dispatch=_sync(skills),
    ),
    Capability(
        id="web_search",
        prompt_fragment=_RESPONSIVENESS,
        functions=tuple(web_search.FUNCTIONS),
        tool_names=frozenset(web_search.TOOL_NAMES),
        dispatch=_threaded(web_search),
    ),
    Capability(
        id="messages",
        # Recovery-only (exposed=False) → explicit-request-only by design.
        tool_names=frozenset(messages.TOOL_NAMES),
        dispatch=_bridged(messages),
        exposed=False,
    ),
    Capability(
        id="memory",
        prompt_fragment=_MEMORY,
        functions=tuple(memory_tools.FUNCTIONS),
        tool_names=frozenset(memory_tools.TOOL_NAMES),
        dispatch=_async_only(memory_tools),
        context_fetcher=memory_tools.shared_memory_context,
    ),
    Capability(
        id="navigation",
        prompt_fragment=_NAVIGATION,
        functions=tuple(navigation.FUNCTIONS),
        tool_names=frozenset(navigation.TOOL_NAMES),
        dispatch=_async_only(navigation),
    ),
    Capability(
        id="reminders",
        prompt_fragment=_REMINDERS,
        functions=tuple(reminders.FUNCTIONS),
        tool_names=frozenset(reminders.TOOL_NAMES),
        dispatch=_sync(reminders),
    ),
    Capability(
        id="assistant",
        # Offline + instant (calculator / converter / world clock).
        functions=tuple(assistant_tools.FUNCTIONS),
        tool_names=frozenset(assistant_tools.TOOL_NAMES),
        dispatch=_sync(assistant_tools),
    ),
)


def identity_fragments() -> str:
    """The feature prose, in registry order, for the session system prompt."""
    return "".join(c.prompt_fragment for c in REGISTRY)


def all_functions() -> list[dict]:
    """Every exposed tool schema, in registry order."""
    return [f for c in REGISTRY if c.exposed for f in c.functions]


async def dispatch(name: str, args: dict, bridge: object = None) -> str | None:
    """Route a tool call to its capability; None if no capability owns it."""
    for cap in REGISTRY:
        if cap.dispatch is not None and name in cap.tool_names:
            return await cap.dispatch(name, args, bridge)
    return None


def tier2_matches(text: str) -> bool:
    """True if any capability declares this utterance engine-tier work."""
    lowered = text.lower()
    return any(re.search(p, lowered) for c in REGISTRY for p in c.tier2_patterns)


async def context_for(query: str) -> list[str]:
    """Gather M2-thinker context from every capability that offers a fetcher."""
    fetchers = [c.context_fetcher for c in REGISTRY if c.context_fetcher]
    if not fetchers:
        return []
    results = await asyncio.gather(
        *(f(query) for f in fetchers), return_exceptions=True
    )
    return [item for r in results if isinstance(r, list) for item in r]
