"""VOICE_MEMORY_V2 flag wiring — history mirror, tools, prompt, off-state."""

from __future__ import annotations

import json

import pytest

import memory_search
import memory_store
import memory_tools
import voice_brain
import voice_history


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_MEMORY_V2", "1")
    monkeypatch.setattr(voice_history, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(voice_history, "_RECENT", tmp_path / "voice_recent.json")
    monkeypatch.setattr(voice_history, "_JOURNAL", tmp_path / "voice_journal.jsonl")


def test_history_add_mirrors_episode():
    voice_history.add("user", "I moved to Tashkent")
    rows = memory_store.episodes_since(0)
    assert [(r, t) for _, r, t in rows] == [("user", "I moved to Tashkent")]


def test_history_add_no_mirror_when_off(monkeypatch):
    monkeypatch.setenv("VOICE_MEMORY_V2", "0")
    voice_history.add("user", "not mirrored")
    assert memory_store.episodes_since(0) == []


def test_remember_dual_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_tools, "MEM_DIR", tmp_path / "memories")
    monkeypatch.setattr(
        memory_tools, "_VOICE_NOTES", tmp_path / "memories" / "voice_notes.md"
    )
    out = json.loads(memory_tools._remember("Avazbek likes dark roast"))
    assert out["status"] == "saved"
    assert "dark roast" in (tmp_path / "memories" / "voice_notes.md").read_text()
    assert "dark roast" in memory_store.live_facts()[0][2]


def test_recall_tool_uses_v2(monkeypatch):
    memory_store.add_fact("Avazbek likes dark roast coffee")
    monkeypatch.setattr(memory_search, "available", lambda: False)  # keyword path
    out = json.loads(memory_tools._recall("coffee roast"))
    assert out["results"] and "dark roast" in out["results"][0]


def test_prompt_includes_v2_profile():
    memory_store.add_fact("Avazbek is training an Uzbek model")
    prompt = voice_brain.build_prompt(seed_history=True)
    assert "training an Uzbek model" in prompt


@pytest.mark.asyncio
async def test_context_fetcher_routes_to_v2(monkeypatch):
    memory_store.add_fact("Avazbek likes dark roast")
    row = memory_search.searchable_rows(("fact",))[0]
    monkeypatch.setattr(memory_search, "search", lambda q, limit=4: [row])
    out = await memory_tools.shared_memory_context("coffee")
    assert out == ["[known] Avazbek likes dark roast"]
