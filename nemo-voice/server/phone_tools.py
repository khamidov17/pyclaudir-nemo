"""Phone-control tools for the voice agent: apps, alarms, timers, Telegram.

Each tool becomes one phone command pushed through the ActionBridge; the app's
PhoneCommandExecutor runs it and the result returns so Nemo confirms out loud.
Kept separate from voice_brain's memory tools so each module stays small.
"""

from __future__ import annotations

import json

FUNCTIONS: list[dict] = [
    {
        "name": "open_app",
        "description": (
            "Open an app on Avazbek's phone by its name — 'Spotify', 'gallery', "
            "'YouTube', 'Telegram'. Use when he asks to open or launch anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "App name as a human says it.",
                }
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "set_alarm",
        "description": (
            "Set an alarm on Avazbek's phone clock. Convert spoken times yourself: "
            "'2 am tomorrow' → hour=2, minutes=0. 24-hour clock."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hour": {"type": "integer", "description": "0-23"},
                "minutes": {"type": "integer", "description": "0-59, default 0"},
                "label": {"type": "string", "description": "Optional alarm label."},
            },
            "required": ["hour"],
        },
    },
    {
        "name": "set_timer",
        "description": "Start a countdown timer on Avazbek's phone. Convert to seconds yourself.",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "Timer length in seconds.",
                },
                "label": {"type": "string", "description": "Optional timer label."},
            },
            "required": ["seconds"],
        },
    },
    {
        "name": "message_contact",
        "description": (
            "Send a Telegram message to someone by name (e.g. 'Aziz'). Opens "
            "Avazbek's Telegram, searches that name in Telegram itself, opens "
            "the top match and sends. Only when he clearly asks you to message "
            "someone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Contact name as saved in his phone.",
                },
                "text": {"type": "string", "description": "The message to send."},
            },
            "required": ["name", "text"],
        },
    },
    {
        "name": "phone_command",
        "description": (
            "Advanced phone control for anything without a dedicated tool. One "
            "command per call: 'ui_tree' (read the screen), 'tap X Y', "
            "'swipe X1 Y1 X2 Y2', 'press back|home', 'type TEXT', 'list_apps'. "
            "Read the screen with ui_tree before tapping IN SERVICE of a task he "
            "asked for. Never use ui_tree/screenshot to read his content unless "
            "he explicitly asked you to read the screen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command line to run."}
            },
            "required": ["command"],
        },
    },
]

PHONE_TOOL_NAMES = {f["name"] for f in FUNCTIONS}


async def dispatch(name: str, args: dict, bridge) -> str:
    """Run one phone tool through the bridge; always returns a JSON string."""
    if bridge is None:
        return json.dumps({"error": "phone not connected"})
    command = _to_command(name, args)
    if command is None:
        return json.dumps({"error": f"bad arguments for {name}"})
    result = await bridge.run(command)
    return json.dumps(result)


def _to_command(name: str, args: dict) -> str | None:
    if name == "open_app":
        app = str(args.get("app_name", "")).strip()
        return f"open {app}" if app else None
    if name == "set_alarm":
        return _alarm_command(args)
    if name == "set_timer":
        return _timer_command(args)
    if name == "message_contact":
        # The app parses "tg_msg <name>|<text>" by splitting on the first '|',
        # so a '|' in the name would corrupt it — strip it from the name only
        # (text after the first '|' is preserved fine).
        contact = str(args.get("name", "")).strip().replace("|", " ")
        text = str(args.get("text", "")).strip()
        if not contact or not text:
            return None
        return f"tg_msg {contact}|{text}"
    if name == "phone_command":
        command = str(args.get("command", "")).strip()
        if not command:
            return None
        # Mirror the device-side verb set so the voice path can't smuggle an
        # unknown command type through (the app rejects unknowns too, but
        # validate here rather than relying solely on the client).
        verb = command.split(" ", 1)[0].lower()
        if verb not in _PHONE_COMMAND_VERBS:
            return None
        return command
    return None


# Verbs the free-form phone_command tool may issue (must match the device-side
# PhoneCommandExecutor switch). Excludes tg_msg/set_alarm/etc. which have their
# own dedicated, validated tools.
_PHONE_COMMAND_VERBS = frozenset(
    {
        "status",
        "ui_tree",
        "screenshot",
        "tap",
        "swipe",
        "press",
        "type",
        "open",
        "list_apps",
    }
)


def _alarm_command(args: dict) -> str | None:
    try:
        hour = int(args["hour"])
        minutes = int(args.get("minutes") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minutes <= 59):
        return None
    label = str(args.get("label", "")).strip()
    return f"set_alarm {hour} {minutes} {label}".strip()


def _timer_command(args: dict) -> str | None:
    try:
        seconds = int(args["seconds"])
    except (KeyError, TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    label = str(args.get("label", "")).strip()
    return f"set_timer {seconds} {label}".strip()
