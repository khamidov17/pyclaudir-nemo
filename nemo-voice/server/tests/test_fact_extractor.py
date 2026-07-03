"""fact_extractor — upsert bands, followups, pointer gating, failure retention."""

from __future__ import annotations

import json

import pytest

import fact_extractor
import memory_search
import memory_store


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(memory_search, "embed_pending", lambda: 0)


def _model_reply(monkeypatch, payload: dict):
    async def fake(_key: str, _convo: str) -> dict:
        return payload

    monkeypatch.setattr(fact_extractor, "_call_model", fake)


def _seed_turns(n: int = 6) -> None:
    for i in range(n):
        memory_store.add_episode("s1", "user", f"turn {i}: I moved to Tashkent")


@pytest.mark.asyncio
async def test_extracts_facts_procedures_followups(monkeypatch):
    _seed_turns()
    monkeypatch.setattr(memory_search, "find_similar_facts", lambda t, k=1: [])
    _model_reply(
        monkeypatch,
        {
            "facts": ["Avazbek moved to Tashkent"],
            "procedures": ["Give morning briefings in Uzbek"],
            "followups": [{"text": "call the clinic", "due": "2026-07-07 10:00"}],
        },
    )
    report = await fact_extractor.extract()
    assert report.facts_added == 1
    assert report.procedures_added == 1
    assert report.followups_added == 1
    assert memory_store.open_followups()[0][1] == "call the clinic"


@pytest.mark.asyncio
async def test_duplicate_fact_skipped(monkeypatch):
    _seed_turns()
    fid = memory_store.add_fact("Avazbek moved to Tashkent")
    row = memory_search.searchable_rows(("fact",))[0]
    monkeypatch.setattr(
        memory_search, "find_similar_facts", lambda t, k=1: [(0.95, row)]
    )
    _model_reply(
        monkeypatch,
        {"facts": ["Avazbek moved to Tashkent"], "procedures": [], "followups": []},
    )
    report = await fact_extractor.extract()
    assert report.facts_added == 0 and report.skipped_dups == 1
    assert [i for i, _, _ in memory_store.live_facts()] == [fid]


@pytest.mark.asyncio
async def test_related_fact_supersedes(monkeypatch):
    _seed_turns()
    old = memory_store.add_fact("Avazbek lives in Samarkand")
    row = memory_search.searchable_rows(("fact",))[0]
    monkeypatch.setattr(
        memory_search, "find_similar_facts", lambda t, k=1: [(0.80, row)]
    )
    _model_reply(
        monkeypatch,
        {"facts": ["Avazbek lives in Tashkent"], "procedures": [], "followups": []},
    )
    report = await fact_extractor.extract()
    assert report.facts_added == 1 and report.facts_superseded == 1
    live = memory_store.live_facts()
    assert old not in [i for i, _, _ in live]
    assert "Tashkent" in live[0][2]


@pytest.mark.asyncio
async def test_gated_below_min_turns(monkeypatch):
    _seed_turns(2)
    called = False

    async def fake(_key: str, _convo: str) -> dict:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(fact_extractor, "_call_model", fake)
    await fact_extractor.extract()
    assert not called


@pytest.mark.asyncio
async def test_model_failure_keeps_pointer(monkeypatch, tmp_path):
    _seed_turns()

    async def boom(_key: str, _convo: str) -> dict:
        raise RuntimeError("api down")

    monkeypatch.setattr(fact_extractor, "_call_model", boom)
    report = await fact_extractor.extract()
    assert report.errors
    assert not (tmp_path / "memory_v2_pointer.json").exists()
    # next run sees the same episodes again
    monkeypatch.setattr(memory_search, "find_similar_facts", lambda t, k=1: [])
    _model_reply(
        monkeypatch, {"facts": ["retry worked"], "procedures": [], "followups": []}
    )
    report2 = await fact_extractor.extract()
    assert report2.facts_added == 1
    assert (
        json.loads((tmp_path / "memory_v2_pointer.json").read_text())["episode_id"] > 0
    )


@pytest.mark.asyncio
async def test_no_key_is_noop(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    _seed_turns()
    report = await fact_extractor.extract()
    assert report.facts_added == 0 and not report.errors
