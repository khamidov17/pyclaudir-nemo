"""After CC auto-compacts, the engine re-seeds the next turn with the last
N messages from the DB so the resumed session stays bounded."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pyclaudir.cc_worker.events import TurnResult
from pyclaudir.config import Config
from pyclaudir.db.database import Database
from pyclaudir.db.messages import fetch_recent_messages, insert_message
from pyclaudir.engine import Engine
from pyclaudir.models import ChatMessage, ControlAction

_CFG = Config.for_test(Path("/tmp"))


def _msg(text: str, mid: int, direction: str = "in") -> ChatMessage:
    return ChatMessage(
        chat_id=-100,
        message_id=mid,
        user_id=42,
        username="alice",
        first_name="Alice",
        direction=direction,  # type: ignore[arg-type]
        timestamp=datetime(2026, 4, 11, 10, 31, tzinfo=timezone.utc),
        text=text,
    )


class FakeWorker:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self._results: asyncio.Queue = asyncio.Queue()

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def inject(self, text: str) -> None:
        pass

    async def flush_deferred_runtime_switch(self) -> None:
        pass

    async def wait_for_result(self):
        return await self._results.get()

    def feed_result(self, result) -> None:
        self._results.put_nowait(result)


@pytest.fixture()
async def db(tmp_path: Path):
    cfg = Config.for_test(tmp_path)
    cfg.ensure_dirs()
    d = await Database.open(cfg.db_path)
    try:
        yield d
    finally:
        await d.close()


@pytest.mark.asyncio
async def test_fetch_recent_messages_oldest_first(db: Database) -> None:
    for i in range(1, 6):
        await insert_message(db, _msg(f"m{i}", mid=i))
    recent = await fetch_recent_messages(db, -100, limit=3)
    assert [r["text"] for r in recent] == ["m3", "m4", "m5"]


@pytest.mark.asyncio
async def test_compacted_turn_reseeds_next_turn(db: Database) -> None:
    for i in range(1, 4):
        await insert_message(db, _msg(f"history-{i}", mid=i))

    worker = FakeWorker()
    eng = Engine(worker, _CFG, debounce_ms=0, db=db)
    await eng.start()
    try:
        stop = ControlAction(action="stop", reason="done")

        # Turn 1: CC reports it auto-compacted.
        await eng.submit(_msg("trigger", mid=10))
        worker.feed_result(TurnResult(control=stop, compacted=True))
        await asyncio.sleep(0.2)

        # Turn 2: the restoration block must be prepended to the prompt.
        await eng.submit(_msg("next", mid=11))
        worker.feed_result(TurnResult(control=stop))
        await asyncio.sleep(0.2)

        assert len(worker.sent) == 2
        second = worker.sent[1]
        assert "<system_note>" in second
        assert "compacted" in second.lower()
        assert "history-3" in second  # recent history re-seeded
        # And it must NOT leak into a later turn.
        await eng.submit(_msg("third", mid=12))
        worker.feed_result(TurnResult(control=stop))
        await asyncio.sleep(0.2)
        assert "<system_note>" not in worker.sent[2]
    finally:
        await eng.stop()
