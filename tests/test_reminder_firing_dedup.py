"""The ``firing`` claim that stops a reminder from being delivered twice.

A reminder stays ``pending`` (the row the engine's on_success commit advances)
until CC actually consumes its turn. The reminder loop polls every ~10s, so a
turn that outlasts the poll interval — a normal nudge that takes a while, or a
delegated coding task that runs for minutes — used to be re-fetched as "due"
and delivered/executed again on every tick. The fix claims the row
``pending`` -> ``firing`` before delivery; only ``pending`` rows are fetched.
These tests pin that contract and its crash-recovery (#22) escape hatches.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pyclaudir.db.database import Database
from pyclaudir.db import reminders as R


async def _db() -> Database:
    return await Database.open(Path(tempfile.mkdtemp()) / "t.db")


_PAST = "2020-01-01 00:00:00"
_NOW = "2025-01-01 00:00:00"


@pytest.mark.asyncio
async def test_claim_is_won_once_then_not_refetched() -> None:
    db = await _db()
    rid = await R.insert_reminder(db, chat_id=1, user_id=1, text="x", trigger_at=_PAST)
    assert len(await R.fetch_due_reminders(db, _NOW)) == 1

    assert await R.claim_reminder_firing(db, rid) is True   # this cycle wins
    assert await R.claim_reminder_firing(db, rid) is False  # a racing cycle loses
    # While firing, the row is no longer "due" — so a slow turn can't be
    # delivered a second time by the next poll.
    assert await R.fetch_due_reminders(db, _NOW) == []
    await db.close()


@pytest.mark.asyncio
async def test_on_failure_reset_makes_it_refireable() -> None:
    db = await _db()
    rid = await R.insert_reminder(db, chat_id=1, user_id=1, text="x", trigger_at=_PAST)
    await R.claim_reminder_firing(db, rid)
    await R.reset_reminder_to_pending(db, rid)  # the on_failure hook
    assert len(await R.fetch_due_reminders(db, _NOW)) == 1
    await db.close()


@pytest.mark.asyncio
async def test_startup_reclaim_recovers_hard_kill() -> None:
    db = await _db()
    rid = await R.insert_reminder(db, chat_id=1, user_id=1, text="x", trigger_at=_PAST)
    await R.claim_reminder_firing(db, rid)  # killed before either hook ran
    assert await R.reset_all_firing_to_pending(db) == 1
    assert len(await R.fetch_due_reminders(db, _NOW)) == 1
    await db.close()


@pytest.mark.asyncio
async def test_one_shot_closes_and_recurring_returns_to_pending() -> None:
    db = await _db()
    one = await R.insert_reminder(db, chat_id=1, user_id=1, text="once", trigger_at=_PAST)
    rec = await R.insert_reminder(
        db, chat_id=1, user_id=1, text="daily", trigger_at=_PAST, cron_expr="0 9 * * *"
    )

    await R.claim_reminder_firing(db, one)
    await R.mark_reminder_sent(db, one)  # one-shot: closed for good
    assert (await R.fetch_reminder_by_id(db, one))["status"] == "sent"

    await R.claim_reminder_firing(db, rec)
    await R.advance_recurring_reminder(db, rec, "2099-01-01 09:00:00")
    row = await R.fetch_reminder_by_id(db, rec)
    assert row["status"] == "pending"  # eligible for its next occurrence
    assert row["trigger_at"] == "2099-01-01 09:00:00"
    await db.close()
