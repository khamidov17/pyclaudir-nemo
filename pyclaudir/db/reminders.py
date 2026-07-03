"""Persistence helpers for the ``reminders`` table."""

from __future__ import annotations

from datetime import datetime, timezone

from .database import Database


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def insert_reminder(
    db: Database,
    *,
    chat_id: int,
    user_id: int,
    text: str,
    trigger_at: str,
    cron_expr: str | None = None,
) -> int:
    """Insert a new reminder and return its id."""
    cursor = await db.connection.execute(
        """
        INSERT INTO reminders (chat_id, user_id, text, trigger_at, cron_expr, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (chat_id, user_id, text, trigger_at, cron_expr, _utcnow_iso()),
    )
    await db.connection.commit()
    return cursor.lastrowid  # type: ignore[return-value]


async def fetch_due_reminders(db: Database, now_utc: str) -> list:
    """Return all pending reminders whose trigger_at <= now_utc.

    Only ``pending`` rows are returned. A row claimed for delivery is moved to
    ``firing`` (see :func:`claim_reminder_firing`) so a turn that outlasts the
    poll interval is not re-fetched and delivered twice.
    """
    return await db.fetch_all(
        "SELECT * FROM reminders WHERE status = 'pending' AND trigger_at <= ?",
        (now_utc,),
    )


async def claim_reminder_firing(db: Database, reminder_id: int) -> bool:
    """Atomically move a pending reminder to ``firing`` before it is delivered.

    Returns True only if this call won the row (it was still ``pending``). The
    ``status = 'pending'`` guard makes the claim a no-op if another poll cycle
    already fired it, which is what stops a slow turn (CC busy longer than the
    poll interval, e.g. a delegated coding task) from being delivered repeatedly.
    """
    cursor = await db.connection.execute(
        "UPDATE reminders SET status = 'firing' WHERE id = ? AND status = 'pending'",
        (reminder_id,),
    )
    await db.connection.commit()
    return cursor.rowcount > 0


async def reset_reminder_to_pending(db: Database, reminder_id: int) -> None:
    """Return a ``firing`` reminder to ``pending`` so the next tick re-fires it.

    Used as the engine ``on_failure`` hook: if the CC turn that was meant to
    consume the reminder crashes, the row must not stay stuck in ``firing``
    (which ``fetch_due_reminders`` never returns). Mirrors the on_success commit
    being dropped — see #22."""
    await db.execute(
        "UPDATE reminders SET status = 'pending' WHERE id = ? AND status = 'firing'",
        (reminder_id,),
    )


async def reset_all_firing_to_pending(db: Database) -> int:
    """Reclaim any rows left ``firing`` by a hard process kill (neither the
    success nor failure hook ran). Called once at startup so a crash mid-delivery
    re-fires on the next run instead of being lost. Returns the count reclaimed.
    """
    cursor = await db.connection.execute(
        "UPDATE reminders SET status = 'pending' WHERE status = 'firing'",
    )
    await db.connection.commit()
    return cursor.rowcount


async def mark_reminder_sent(db: Database, reminder_id: int) -> None:
    """Mark a one-shot reminder as sent."""
    await db.execute(
        "UPDATE reminders SET status = 'sent' WHERE id = ?",
        (reminder_id,),
    )


async def advance_recurring_reminder(
    db: Database, reminder_id: int, next_trigger_at: str
) -> None:
    """Advance a recurring reminder to its next occurrence and return it to the
    ``pending`` pool.

    The row was moved to ``firing`` when it was claimed for delivery; resetting
    the status here is what makes it eligible for the next fire. Without it a
    recurring reminder would fire exactly once and then sit in ``firing``
    forever."""
    await db.execute(
        "UPDATE reminders SET trigger_at = ?, status = 'pending' WHERE id = ?",
        (next_trigger_at, reminder_id),
    )


async def cancel_reminder(db: Database, reminder_id: int) -> bool:
    """Cancel a pending reminder. Returns True if a row was updated."""
    cursor = await db.connection.execute(
        "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
        (reminder_id,),
    )
    await db.connection.commit()
    return cursor.rowcount > 0


async def list_pending_reminders(db: Database, chat_id: int) -> list:
    """Return all pending reminders for a given chat."""
    return await db.fetch_all(
        "SELECT * FROM reminders WHERE chat_id = ? AND status = 'pending' ORDER BY trigger_at",
        (chat_id,),
    )


async def pending_with_auto_seed_key(db: Database, key: str) -> int:
    """Count PENDING reminders tagged with the given auto_seed_key.

    Used by the startup seed hook. Only pending rows count as "exists"
    — a cancelled or sent row means the reminder is not currently
    active and the startup hook should re-seed. This is the "learning
    cannot be stopped" guarantee: even if something (bot, operator,
    manual SQL) cancels the self-reflection reminder, the next restart
    re-seeds it.
    """
    row = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM reminders WHERE auto_seed_key = ? AND status = 'pending'",
        (key,),
    )
    return int(row["c"]) if row is not None else 0


async def any_with_auto_seed_key(db: Database, key: str) -> int:
    """Count reminders with this ``auto_seed_key`` in ANY status.

    Used for optional seeded reminders (e.g. briefings) that should be
    installed once and then STAY however the user left them — unlike the
    mandatory loops (:func:`pending_with_auto_seed_key`) which re-seed when
    cancelled. If the user cancels a briefing, this still counts the row, so
    the next startup won't resurrect it.
    """
    row = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM reminders WHERE auto_seed_key = ?",
        (key,),
    )
    return int(row["c"]) if row is not None else 0


async def fetch_reminder_by_id(db: Database, reminder_id: int) -> dict | None:
    """Fetch a single reminder by id, or None if not found."""
    row = await db.fetch_one(
        "SELECT id, chat_id, user_id, text, trigger_at, cron_expr, status, "
        "auto_seed_key FROM reminders WHERE id = ?",
        (reminder_id,),
    )
    if row is None:
        return None
    return dict(row)


async def insert_auto_seeded_reminder(
    db: Database,
    *,
    auto_seed_key: str,
    chat_id: int,
    user_id: int,
    text: str,
    trigger_at: str,
    cron_expr: str | None = None,
) -> int:
    """Insert a default reminder tagged with an ``auto_seed_key``.

    Same columns as :func:`insert_reminder` plus the ``auto_seed_key``
    so future startup checks can see this row already exists.
    """
    cursor = await db.connection.execute(
        """
        INSERT INTO reminders (
            chat_id, user_id, text, trigger_at, cron_expr,
            status, created_at, auto_seed_key
        )
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (chat_id, user_id, text, trigger_at, cron_expr, _utcnow_iso(), auto_seed_key),
    )
    await db.connection.commit()
    return cursor.lastrowid  # type: ignore[return-value]
