"""memory_search — hybrid search, keyword fallback, dedup check, embed_pending.

Embeddings are faked with deterministic per-text vectors so tests run offline:
same text → same vector; related texts share a seeded direction.
"""

from __future__ import annotations

import pytest

import memory_search
import memory_store

_VECS = {
    "coffee": [1.0, 0.0, 0.0],
    "coffee-near": [0.95, 0.31, 0.0],
    "weather": [0.0, 1.0, 0.0],
}


def _fake_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        low = t.lower()
        if "coffee" in low or "roast" in low:
            out.append(_VECS["coffee-near"] if "dark" in low else _VECS["coffee"])
        elif "rain" in low or "weather" in low:
            out.append(_VECS["weather"])
        else:
            out.append([0.0, 0.0, 1.0])
    return out


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(memory_search, "embed", _fake_embed)
    monkeypatch.setattr(memory_search, "available", lambda: True)


def test_semantic_search_finds_by_meaning():
    memory_store.add_fact("Avazbek likes dark roast")
    memory_store.add_fact("It might rain tomorrow")
    memory_search.embed_pending()
    rows = memory_search.search("what coffee does he drink")
    assert rows and "roast" in rows[0].text
    assert all("rain" not in r.text for r in rows)


def test_keyword_fallback_when_embeddings_down(monkeypatch):
    memory_store.add_fact("Avazbek likes dark roast coffee")
    monkeypatch.setattr(memory_search, "available", lambda: False)
    rows = memory_search.search("coffee roast")
    assert rows and "roast" in rows[0].text


def test_fact_outranks_episode():
    memory_store.add_fact("Avazbek drinks coffee daily")
    memory_store.add_episode("s1", "user", "let's get coffee")
    memory_search.embed_pending()
    rows = memory_search.search("coffee")
    assert rows[0].kind == "fact"


def test_find_similar_facts_ranks_duplicates_first():
    memory_store.add_fact("Avazbek likes coffee")
    memory_store.add_fact("It might rain tomorrow")
    memory_search.embed_pending()
    hits = memory_search.find_similar_facts("Avazbek likes coffee")
    assert hits and hits[0][0] == pytest.approx(1.0)
    assert "coffee" in hits[0][1].text


def test_embed_pending_counts_and_is_idempotent():
    memory_store.add_fact("Avazbek likes coffee")
    memory_store.add_episode("s1", "user", "weather is nice")
    assert memory_search.embed_pending() == 2
    assert memory_search.embed_pending() == 0


def test_empty_query_returns_nothing():
    assert memory_search.search("  ") == []
