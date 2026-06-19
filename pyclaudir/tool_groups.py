from __future__ import annotations

import logging
from dataclasses import dataclass

from .cc_worker.spec import CORE_ALLOWED_TOOLS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolModule:
    name: str
    keywords: tuple[str, ...]
    tools: tuple[str, ...]


TOOL_MODULES: tuple[ToolModule, ...] = (
    ToolModule(
        name="gmail",
        keywords=("email", "gmail", "inbox", "mail", "unread", "attachment"),
        tools=(
            "gmail_read",
            "gmail_search",
            "gmail_send",
            "gmail_get",
            "gmail_reply",
            "gmail_forward",
            "gmail_mark",
            "gmail_label",
        ),
    ),
    ToolModule(
        name="drive",
        keywords=("drive", "gdrive", "upload", "download"),
        tools=(
            "drive_search",
            "drive_create_file",
            "drive_create_folder",
            "drive_move_file",
            "drive_rename",
            "drive_delete",
            "drive_share",
            "drive_list_folder",
        ),
    ),
    ToolModule(
        name="calendar",
        keywords=("calendar", "event", "schedule", "meeting"),
        tools=(
            "calendar_get_events",
            "calendar_create_event",
            "calendar_update_event",
            "calendar_delete_event",
        ),
    ),
    ToolModule(
        name="slack",
        keywords=("slack", "channel", "workspace"),
        tools=("slack_read", "slack_send"),
    ),
    ToolModule(
        name="memory_extended",
        keywords=("remember", "forget", "memory", "recall", "note", "wiki", "gbrain"),
        tools=(
            "mcp__pyclaudir__list_memories",
            "mcp__pyclaudir__read_memory",
            "mcp__pyclaudir__write_memory",
            "mcp__pyclaudir__append_memory",
            "mcp__pyclaudir__synthesize_memory_wiki",
            "mcp__pyclaudir__search_memories",
        ),
    ),
    ToolModule(
        name="reminder_extended",
        keywords=("remind", "reminder", "alarm", "schedule"),
        tools=(
            "mcp__pyclaudir__set_reminder",
            "mcp__pyclaudir__list_reminders",
            "mcp__pyclaudir__cancel_reminder",
        ),
    ),
    ToolModule(
        name="render",
        keywords=("pdf", "render", "document", "latex", "html"),
        tools=(
            "mcp__pyclaudir__render_html",
            "mcp__pyclaudir__render_latex",
            "mcp__pyclaudir__render_markdown",
        ),
    ),
    ToolModule(
        name="phone",
        keywords=(
            "phone",
            "android",
            "screen",
            "tap",
            "swipe",
            "type this",
            "open app",
            "screenshot",
        ),
        tools=("mcp__pyclaudir__phone_action",),
    ),
    ToolModule(
        name="voice",
        keywords=("voice", "speak", "say this", "audio", "tts"),
        tools=("mcp__pyclaudir__send_voice_message",),
    ),
    ToolModule(
        name="query",
        keywords=("database", "query", "sql", "db", "history", "messages"),
        tools=("mcp__pyclaudir__query_db",),
    ),
    ToolModule(
        name="web",
        keywords=(
            "search",
            "web",
            "find online",
            "look up",
            "current",
            "latest",
            "news",
        ),
        tools=("WebFetch", "WebSearch"),
    ),
)

INTENT_TOOLS: dict[str, tuple[str, ...]] = {
    "SIMPLE_REPLY": (),
    "MEMORY": (
        "mcp__pyclaudir__list_memories",
        "mcp__pyclaudir__read_memory",
        "mcp__pyclaudir__write_memory",
        "mcp__pyclaudir__append_memory",
        "mcp__pyclaudir__synthesize_memory_wiki",
        "mcp__pyclaudir__search_memories",
    ),
    "REMINDER": (
        "mcp__pyclaudir__set_reminder",
        "mcp__pyclaudir__list_reminders",
        "mcp__pyclaudir__cancel_reminder",
    ),
    "WEB": ("mcp__pyclaudir__fetch_url", "WebSearch"),
    "DOCUMENT": (
        "mcp__pyclaudir__render_html",
        "mcp__pyclaudir__render_latex",
        "mcp__pyclaudir__send_photo",
        "mcp__pyclaudir__read_attachment",
    ),
    "CODEX": ("mcp__codex",),
    "FULL_NEMO": (
        "mcp__pyclaudir__search_memories",
        "mcp__pyclaudir__read_memory",
    ),
}


def detect_extra_tools(text: str) -> frozenset[str]:
    """Scan text for module keywords and return the union of matched tool names."""
    lowered = text.lower()
    matched: set[str] = set()
    for module in TOOL_MODULES:
        if any(kw in lowered for kw in module.keywords):
            matched.update(module.tools)
            logger.debug("tool_groups: matched module %r", module.name)
    return frozenset(matched)


def build_allowed_tools(text: str, base: tuple[str, ...]) -> tuple[str, ...]:
    """Combine base tools with extras detected from text, deduped and sorted."""
    extras = detect_extra_tools(text)
    combined = frozenset(base) | extras
    return tuple(sorted(combined))


# Tools that can control the phone or read the message/SQL store. Keyword
# detection alone must never expose these to a non-owner: even if the access
# policy is loosened to allowlist/open, only the owner's own messages may
# summon them, so a crafted group message can't prompt-inject phone control.
OWNER_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "mcp__pyclaudir__phone_action",
        "mcp__pyclaudir__query_db",
    }
)


def build_turn_tools(
    text: str,
    *,
    intent: str = "FULL_NEMO",
    external_tools: tuple[str, ...] = (),
    base: tuple[str, ...] = CORE_ALLOWED_TOOLS,
    is_owner: bool = True,
) -> tuple[str, ...]:
    """Return the minimal allowed tool set for one routed turn.

    ``is_owner`` gates phone/SQL tools: non-owner senders never get them,
    independent of the access policy (defense-in-depth against prompt
    injection from group messages).
    """
    extras = set(INTENT_TOOLS.get(intent, ()))
    extras.update(detect_extra_tools(text))
    if intent == "CODEX":
        extras.update(t for t in external_tools if t.startswith("mcp__codex"))
    combined = frozenset(base) | extras
    if not is_owner:
        combined -= OWNER_ONLY_TOOLS
    return tuple(sorted(combined))
