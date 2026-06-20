"""Voice-side reminder tools — let the spoken Nemo schedule reminders.

The Qwen voice agent runs in this process; the engine (pyclaudir) runs in
another. They share one SQLite file (``data/pyclaudir.db``), so creating a
reminder here is just an INSERT into the same ``reminders`` table the engine's
60s reminder loop already polls. When it fires, the engine composes a spoken
nudge and Edge-TTS plays it on the phone — no new delivery path needed.

Kept separate from voice_brain so the schemas + dispatch don't bloat it.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger("nemo.reminders")

_DATA_DIR = Path(
    os.environ.get("NEMO_VOICE_DATA_DIR")
    or (Path(__file__).resolve().parents[2] / "data")
)
_DB = _DATA_DIR / "pyclaudir.db"
_OFFSET_HOURS = int(os.environ.get("NEMO_UTC_OFFSET", "5"))
# The owner's Telegram chat — reminders route here so the engine speaks them
# back to the phone. Same env the rest of voice_brain uses.
_CHAT_ID = os.environ.get("NEMO_DEFAULT_CHAT_ID", "").strip()

_FMT = "%Y-%m-%d %H:%M:%S"

TOOL_NAMES = {"set_reminder", "list_reminders", "cancel_reminder"}

FUNCTIONS: list[dict] = [
    {
        "name": "set_reminder",
        "description": (
            "Schedule a spoken reminder for Avazbek. At the chosen time Nemo "
            "will speak it out loud on his phone. Give EXACTLY ONE of: "
            "`in_minutes` (e.g. 30), `local_time` (today/any date as "
            "'YYYY-MM-DD HH:MM' in HIS local time — you know the date from "
            "get_time), or `daily_time` ('HH:MM' local, for an every-day "
            "reminder like a morning wake-up)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "What to remind him about, e.g. 'call Aziz'.",
                },
                "in_minutes": {
                    "type": "integer",
                    "description": "Fire this many minutes from now.",
                },
                "local_time": {
                    "type": "string",
                    "description": "Local datetime 'YYYY-MM-DD HH:MM' for a one-shot.",
                },
                "daily_time": {
                    "type": "string",
                    "description": "Local 'HH:MM' to repeat every day.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "list_reminders",
        "description": "List Avazbek's pending reminders so he knows what's scheduled.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a pending reminder by its id (from list_reminders).",
        "parameters": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "integer", "description": "The id to cancel."}
            },
            "required": ["reminder_id"],
        },
    },
]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(_DB), timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def _trigger_from_args(args: dict) -> tuple[str, str | None]:
    """Resolve (trigger_at_utc, cron_expr) from the voice tool args.

    Raises ValueError with a spoken-friendly message on bad/missing input.
    """
    if args.get("in_minutes") is not None:
        mins = int(args["in_minutes"])
        if mins <= 0:
            raise ValueError("the time has to be in the future")
        return (_now_utc() + timedelta(minutes=mins)).strftime(_FMT), None
    if args.get("daily_time"):
        return _daily(args["daily_time"])
    if args.get("local_time"):
        return _one_shot_local(args["local_time"]), None
    raise ValueError("tell me when — in how many minutes, a time, or every day")


def _one_shot_local(local_str: str) -> str:
    """Local 'YYYY-MM-DD HH:MM' → future UTC string."""
    local = datetime.strptime(local_str.strip(), "%Y-%m-%d %H:%M")
    utc = local - timedelta(hours=_OFFSET_HOURS)
    utc = utc.replace(tzinfo=timezone.utc)
    if utc <= _now_utc():
        raise ValueError("that time has already passed")
    return utc.strftime(_FMT)


def _daily(hhmm: str) -> tuple[str, str]:
    """Local 'HH:MM' → (first-trigger UTC, daily cron in UTC)."""
    hh, mm = (int(x) for x in hhmm.strip().split(":"))
    utc_hour = (hh - _OFFSET_HOURS) % 24
    cron = f"{mm} {utc_hour} * * *"
    first = _next_cron(cron)
    return first, cron


def _next_cron(cron: str) -> str:
    """First future UTC trigger for a cron expr (best-effort without croniter)."""
    try:
        from croniter import croniter

        return croniter(cron, _now_utc()).get_next(datetime).strftime(_FMT)
    except ImportError:  # pragma: no cover
        return (_now_utc() + timedelta(minutes=1)).strftime(_FMT)


def dispatch(name: str, args: dict) -> str:
    """Run a reminder tool; always returns a JSON string for the voice agent."""
    try:
        if not _CHAT_ID:
            return json.dumps({"error": "reminders aren't configured"})
        if name == "set_reminder":
            return _set(args)
        if name == "list_reminders":
            return _list()
        if name == "cancel_reminder":
            return _cancel(int(args.get("reminder_id", 0)))
        return json.dumps({"error": f"unknown reminder tool {name}"})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 — never crash the voice turn
        LOG.exception("reminder tool %s failed", name)
        return json.dumps({"error": str(exc)})


def _set(args: dict) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        return json.dumps({"error": "what should I remind you about?"})
    trigger_at, cron = _trigger_from_args(args)
    owner = int(_CHAT_ID)
    con = _connect()
    try:
        cur = con.execute(
            "INSERT INTO reminders "
            "(chat_id, user_id, text, trigger_at, cron_expr, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (owner, owner, text, trigger_at, cron, _now_utc().strftime(_FMT)),
        )
        con.commit()
        rid = cur.lastrowid
    finally:
        con.close()
    LOG.info("voice set reminder #%s at %s (cron=%s)", rid, trigger_at, cron)
    kind = "every day" if cron else "once"
    return json.dumps(
        {"status": "set", "id": rid, "when_utc": trigger_at, "repeat": kind}
    )


def _list() -> str:
    con = _connect()
    try:
        rows = con.execute(
            "SELECT id, text, trigger_at, cron_expr FROM reminders "
            "WHERE chat_id = ? AND status = 'pending' ORDER BY trigger_at",
            (int(_CHAT_ID),),
        ).fetchall()
    finally:
        con.close()
    items = [
        {
            "id": r["id"],
            "text": r["text"],
            "when_utc": r["trigger_at"],
            "daily": bool(r["cron_expr"]),
        }
        for r in rows
    ]
    return json.dumps({"reminders": items})


def _cancel(reminder_id: int) -> str:
    con = _connect()
    try:
        # Don't let voice cancel the engine's mandatory auto-seeded loops.
        row = con.execute(
            "SELECT auto_seed_key FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
        if (
            row is not None
            and row["auto_seed_key"]
            and "brief" not in row["auto_seed_key"]
        ):
            return json.dumps({"error": "that's a system reminder, I can't cancel it"})
        cur = con.execute(
            "UPDATE reminders SET status = 'cancelled' "
            "WHERE id = ? AND status = 'pending'",
            (reminder_id,),
        )
        con.commit()
        ok = cur.rowcount > 0
    finally:
        con.close()
    return json.dumps({"status": "cancelled" if ok else "not_found", "id": reminder_id})
