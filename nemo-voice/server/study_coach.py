"""Learning coach — SM-2 spaced repetition, quizzed by voice.

Cards come from "quiz me on this: front / back" or the study-assistant
skill's flashcards. `quiz_me` serves the most overdue card; Nemo asks it,
hears the answer, and calls `grade_card` (0-5 quality). The SM-2 update
schedules the next review. A watcher nudges when cards pile up
(interrupt policy decides if it may speak). See docs/design-translator-nav.md.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import memory_store

LOG = logging.getLogger("nemo.study_coach")

_UTC_OFFSET_HOURS = int(os.environ.get("NEMO_UTC_OFFSET", "5"))
_NUDGE_MIN_DUE = int(os.environ.get("STUDY_NUDGE_MIN_DUE", "5"))
_NUDGE_EVERY_HOURS = float(os.environ.get("STUDY_NUDGE_EVERY_HOURS", "6"))

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS study_items ("
    " id INTEGER PRIMARY KEY,"
    " front TEXT NOT NULL,"
    " back TEXT NOT NULL,"
    " due_ts TEXT NOT NULL,"
    " ease REAL NOT NULL DEFAULT 2.5,"
    " interval_days REAL NOT NULL DEFAULT 0,"
    " reps INTEGER NOT NULL DEFAULT 0);"
    "CREATE TABLE IF NOT EXISTS study_meta ("
    " key TEXT PRIMARY KEY, value TEXT NOT NULL)"
)

FUNCTIONS: list[dict] = [
    {
        "name": "add_flashcard",
        "description": (
            "Save something Avazbek wants to memorize as a flashcard "
            "('quiz me on this later'). Front = the question/word, back = the answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "front": {"type": "string", "description": "Question side."},
                "back": {"type": "string", "description": "Answer side."},
            },
            "required": ["front", "back"],
        },
    },
    {
        "name": "quiz_me",
        "description": (
            "Get the next due flashcard when he asks to be quizzed. Ask him the "
            "front, judge his spoken answer against the back, then call "
            "grade_card with quality 0-5 (5=perfect, 3=hesitant, 0=blank)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "grade_card",
        "description": "Record how well he answered the card quiz_me served.",
        "parameters": {
            "type": "object",
            "properties": {
                "card_id": {"type": "integer"},
                "quality": {"type": "integer", "description": "0-5."},
            },
            "required": ["card_id", "quality"],
        },
    },
]
TOOL_NAMES = {f["name"] for f in FUNCTIONS}


def _connect():
    con = memory_store.connect()
    con.executescript(_SCHEMA)
    return con


def _now_local() -> datetime:
    # Naive local time — compared against strptime-parsed (naive) meta values.
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        hours=_UTC_OFFSET_HOURS
    )


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def add_card(front: str, back: str) -> int:
    con = _connect()
    try:
        cur = con.execute(
            "INSERT INTO study_items (front, back, due_ts) VALUES (?, ?, ?)",
            (front[:300], back[:300], _fmt(_now_local())),
        )
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def due_cards(limit: int = 50) -> list[tuple[int, str, str]]:
    con = _connect()
    try:
        rows = con.execute(
            "SELECT id, front, back FROM study_items WHERE due_ts <= ?"
            " ORDER BY due_ts LIMIT ?",
            (_fmt(_now_local()), limit),
        ).fetchall()
        return [(int(i), str(f), str(b)) for i, f, b in rows]
    finally:
        con.close()


def grade(card_id: int, quality: int) -> bool:
    """SM-2: quality < 3 resets the card; otherwise the interval grows by the
    ease factor, which itself drifts with answer quality."""
    quality = max(0, min(5, quality))
    con = _connect()
    try:
        row = con.execute(
            "SELECT ease, interval_days, reps FROM study_items WHERE id = ?",
            (card_id,),
        ).fetchone()
        if row is None:
            return False
        ease, interval, reps = float(row[0]), float(row[1]), int(row[2])
        if quality < 3:
            interval, reps = 0.007, 0  # ~10 minutes, start over
        else:
            interval = {0: 1.0, 1: 6.0}.get(reps, interval * ease)
            reps += 1
            ease = max(1.3, ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        due = _fmt(_now_local() + timedelta(days=interval))
        con.execute(
            "UPDATE study_items SET ease=?, interval_days=?, reps=?, due_ts=? WHERE id=?",
            (ease, interval, reps, due, card_id),
        )
        con.commit()
        return True
    finally:
        con.close()


def _tool_add(args: dict) -> dict:
    front = str(args.get("front") or "").strip()
    back = str(args.get("back") or "").strip()
    if not front or not back:
        return {"error": "need both front and back"}
    add_card(front, back)
    return {"status": "card saved", "due_now": len(due_cards())}


def _tool_quiz() -> dict:
    cards = due_cards(limit=1)
    if not cards:
        return {"status": "nothing due — all caught up"}
    cid, front, back = cards[0]
    return {
        "card_id": cid,
        "ask_him": front,
        "correct_answer": back,
        "remaining_due": len(due_cards()),
    }


def _tool_grade(args: dict) -> dict:
    ok = grade(int(args.get("card_id") or 0), int(args.get("quality") or 0))
    return {"status": "graded" if ok else "unknown card"}


async def dispatch(name: str, args: dict) -> str:
    if name == "add_flashcard":
        return json.dumps(_tool_add(args))
    if name == "quiz_me":
        return json.dumps(_tool_quiz())
    if name == "grade_card":
        return json.dumps(_tool_grade(args))
    return json.dumps({"error": f"unknown study tool {name}"})


# ── proactive watcher (registered in watchers.WATCHERS) ─────────────────────


def _last_nudge() -> datetime | None:
    con = _connect()
    try:
        row = con.execute(
            "SELECT value FROM study_meta WHERE key='last_nudge'"
        ).fetchone()
        return datetime.strptime(row[0], "%Y-%m-%d %H:%M") if row else None
    finally:
        con.close()


def poll_study() -> list:
    """One nudge when enough cards are due, at most every N hours."""
    import watchers

    due = len(due_cards())
    if due < _NUDGE_MIN_DUE:
        return []
    last = _last_nudge()
    if last and _now_local() - last < timedelta(hours=_NUDGE_EVERY_HOURS):
        return []
    return [
        watchers.Event(
            "study",
            "normal",
            f"{due} flashcards are due — a quick quiz round?",
            f"study:{_fmt(_now_local())[:13]}",
            due,
        )
    ]


def ack_study(_event) -> None:
    con = _connect()
    try:
        con.execute(
            "INSERT OR REPLACE INTO study_meta (key, value) VALUES ('last_nudge', ?)",
            (_fmt(_now_local()),),
        )
        con.commit()
    finally:
        con.close()
