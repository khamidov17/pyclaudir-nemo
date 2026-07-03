"""consolidate — daily gating, insight/people/mood writes, failure skip."""

from __future__ import annotations

import pytest

import consolidate
import memory_search
import memory_store


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(memory_search, "embed_pending", lambda: 0)
    monkeypatch.setattr(consolidate, "_HOUR", 0)  # always past the hour
    return tmp_path


def _seed(n: int = 12) -> None:
    for i in range(n):
        memory_store.add_episode("s1", "user", f"turn {i}: fine-tune stressi")


def _reply(monkeypatch, payload: dict):
    async def fake(_key, _convo):
        return payload

    monkeypatch.setattr(consolidate, "_call_model", fake)


@pytest.mark.asyncio
async def test_writes_insights_people_mood(monkeypatch):
    _seed()
    _reply(
        monkeypatch,
        {
            "insights": ["He has been stressed about the fine-tune all week"],
            "people": [{"name": "Aziz", "note": "close friend, plays football"}],
            "mood": "long tiring day but hopeful",
        },
    )
    written = await consolidate.maybe_run()
    assert written == 3
    facts = memory_store.live_facts()
    subjects = {s for _, s, _ in facts}
    assert "insight" in subjects and "person:Aziz" in subjects
    assert any(s.startswith("mood:") for s in subjects)


@pytest.mark.asyncio
async def test_runs_once_per_day(monkeypatch):
    _seed()
    _reply(monkeypatch, {"insights": ["x"], "people": [], "mood": None})
    assert await consolidate.maybe_run() == 1
    assert await consolidate.maybe_run() == 0  # already ran today


@pytest.mark.asyncio
async def test_quiet_day_skips_without_model_call(monkeypatch):
    _seed(3)  # below _MIN_EPISODES
    called = []

    async def fake(_key, _convo):
        called.append(1)
        return {}

    monkeypatch.setattr(consolidate, "_call_model", fake)
    assert await consolidate.maybe_run() == 0
    assert not called
    assert not consolidate.due()  # marked done for today


@pytest.mark.asyncio
async def test_model_failure_skips_today(monkeypatch):
    _seed()

    async def boom(_key, _convo):
        raise RuntimeError("api down")

    monkeypatch.setattr(consolidate, "_call_model", boom)
    assert await consolidate.maybe_run() == 0
    assert not consolidate.due()
