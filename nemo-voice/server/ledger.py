"""Voice ledger — expenses and habits logged by voice, summarized from SQL.

"50 ming tushlikka ketdi" → one row; "how much did I spend on food this
month" → a SUM over real rows, not the model's imagination. Lives in
memory_v2.db next to the other memory tables. Amounts default to
NEMO_CURRENCY (UZS). See docs/design-translator-nav.md.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import memory_store

LOG = logging.getLogger("nemo.ledger")

_CURRENCY = os.environ.get("NEMO_CURRENCY", "UZS")
_UTC_OFFSET_HOURS = int(os.environ.get("NEMO_UTC_OFFSET", "5"))

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS ledger ("
    " id INTEGER PRIMARY KEY,"
    " ts TEXT NOT NULL,"
    " kind TEXT NOT NULL,"  # 'expense' | 'habit'
    " amount REAL NOT NULL DEFAULT 0,"
    " currency TEXT NOT NULL DEFAULT '',"
    " category TEXT NOT NULL DEFAULT '',"
    " note TEXT NOT NULL DEFAULT '')"
)

_PERIODS = {"today": 1, "week": 7, "month": 30}

FUNCTIONS: list[dict] = [
    {
        "name": "log_expense",
        "description": (
            "Record money Avazbek spent, the moment he mentions it ('50 ming "
            "tushlikka ketdi'). Amount in plain numbers (50 ming = 50000)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "How much."},
                "category": {
                    "type": "string",
                    "description": "One word: food, transport, shopping, bills, fun, health, other.",
                },
                "note": {"type": "string", "description": "Optional short note."},
                "currency": {"type": "string", "description": f"Default {_CURRENCY}."},
            },
            "required": ["amount", "category"],
        },
    },
    {
        "name": "log_habit",
        "description": (
            "Record a habit event he mentions — gym, run, sleep hours, reading. "
            "('went to the gym' → name=gym; 'slept 6 hours' → name=sleep, amount=6)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Habit name, one word."},
                "amount": {"type": "number", "description": "Optional quantity."},
                "note": {"type": "string", "description": "Optional short note."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "ledger_summary",
        "description": (
            "Totals from the real ledger: 'how much did I spend this week', "
            "'how often did I hit the gym this month'. Never guess amounts — "
            "call this."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["today", "week", "month"]},
                "category": {
                    "type": "string",
                    "description": "Optional expense category or habit name filter.",
                },
            },
            "required": ["period"],
        },
    },
]
TOOL_NAMES = {f["name"] for f in FUNCTIONS}


def _connect():
    con = memory_store.connect()
    con.execute(_SCHEMA)
    return con


def _now_local() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=_UTC_OFFSET_HOURS)


@dataclass(frozen=True)
class Entry:
    kind: str  # 'expense' | 'habit'
    amount: float
    category: str
    currency: str = ""
    note: str = ""


def _add(entry: Entry) -> int:
    con = _connect()
    try:
        cur = con.execute(
            "INSERT INTO ledger (ts, kind, amount, currency, category, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now_local().strftime("%Y-%m-%d %H:%M"),
                entry.kind,
                entry.amount,
                entry.currency[:8],
                entry.category.lower()[:30],
                entry.note[:200],
            ),
        )
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def _summary(period: str, category: str) -> dict:
    days = _PERIODS.get(period, 7)
    since = (_now_local() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    where, params = "ts >= ?", [since]
    if category:
        where += " AND category = ?"
        params.append(category.lower())
    con = _connect()
    try:
        expenses = con.execute(
            f"SELECT currency, SUM(amount), COUNT(*) FROM ledger"
            f" WHERE kind='expense' AND {where} GROUP BY currency",
            params,
        ).fetchall()
        by_cat = con.execute(
            f"SELECT category, currency, SUM(amount) FROM ledger"
            f" WHERE kind='expense' AND {where} GROUP BY category, currency"
            f" ORDER BY SUM(amount) DESC LIMIT 6",
            params,
        ).fetchall()
        habits = con.execute(
            f"SELECT category, COUNT(*), SUM(amount) FROM ledger"
            f" WHERE kind='habit' AND {where} GROUP BY category",
            params,
        ).fetchall()
    finally:
        con.close()
    return {
        "period": period,
        "spent": [
            {"currency": c or _CURRENCY, "total": t, "entries": n}
            for c, t, n in expenses
        ],
        "top_categories": [
            {"category": c, "currency": cur or _CURRENCY, "total": t}
            for c, cur, t in by_cat
        ],
        "habits": [{"name": c, "times": n, "total_amount": t} for c, n, t in habits],
    }


def _num(value: object) -> float:
    """Coerce an LLM-supplied number defensively (it may pass '50k' / 'one')."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _tool_expense(args: dict) -> dict:
    amount = _num(args.get("amount"))
    if amount <= 0:
        return {"error": "amount must be positive"}
    _add(
        Entry(
            kind="expense",
            amount=amount,
            category=str(args.get("category") or "other"),
            currency=str(args.get("currency") or _CURRENCY),
            note=str(args.get("note") or ""),
        )
    )
    return {"status": "logged"}


def _tool_habit(args: dict) -> dict:
    habit = str(args.get("name") or "").strip()
    if not habit:
        return {"error": "no habit name"}
    _add(
        Entry(
            kind="habit",
            amount=_num(args.get("amount")),
            category=habit,
            note=str(args.get("note") or ""),
        )
    )
    return {"status": "logged"}


def recent_habits(name: str, days: int) -> list[tuple[str, float]]:
    """(ts, amount) for a habit within `days`, newest first — the health
    watcher's read side. Empty when the ledger has no such rows yet."""
    since = (_now_local() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    con = _connect()
    try:
        rows = con.execute(
            "SELECT ts, amount FROM ledger WHERE kind='habit' AND category=?"
            " AND ts >= ? ORDER BY ts DESC",
            (name.lower(), since),
        ).fetchall()
        return [(str(t), float(a)) for t, a in rows]
    finally:
        con.close()


async def dispatch(name: str, args: dict) -> str:
    if name == "log_expense":
        return json.dumps(_tool_expense(args))
    if name == "log_habit":
        return json.dumps(_tool_habit(args))
    if name == "ledger_summary":
        return json.dumps(
            _summary(str(args.get("period") or "week"), str(args.get("category") or ""))
        )
    return json.dumps({"error": f"unknown ledger tool {name}"})
