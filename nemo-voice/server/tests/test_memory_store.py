"""memory_store — schema, CRUD, supersede chain, followup lifecycle."""

from __future__ import annotations

import pytest

import memory_search
import memory_store


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))


def test_add_episode_and_since():
    a = memory_store.add_episode("s1", "user", "I moved to Tashkent")
    b = memory_store.add_episode("s1", "assistant", "Noted!")
    assert b > a > 0
    rows = memory_store.episodes_since(0)
    assert [(r, t) for _, r, t in rows] == [
        ("user", "I moved to Tashkent"),
        ("assistant", "Noted!"),
    ]
    assert memory_store.episodes_since(a) == [(b, "assistant", "Noted!")]


def test_empty_episode_rejected():
    assert memory_store.add_episode("s1", "user", "   ") == 0
    assert memory_store.episodes_since(0) == []


def test_fact_supersede_hides_old():
    old = memory_store.add_fact("Avazbek drinks light roast")
    new = memory_store.add_fact("Avazbek drinks dark roast now")
    memory_store.supersede_fact(old, new)
    live = memory_store.live_facts()
    ids = [i for i, _, _ in live]
    assert new in ids and old not in ids


def test_superseded_fact_excluded_from_search_rows():
    old = memory_store.add_fact("old fact about coffee")
    new = memory_store.add_fact("new fact about coffee")
    memory_store.supersede_fact(old, new)
    rows = memory_search.searchable_rows(("fact",))
    assert [r.id for r in rows] == [new]


def test_followup_lifecycle():
    fid = memory_store.add_followup("call the clinic", "2026-07-07 10:00", 3)
    memory_store.add_followup("no due date", None)
    open_ = memory_store.open_followups()
    assert open_[0][0] == fid  # dated followups sort before undated
    memory_store.resolve_followup(fid)
    assert fid not in [i for i, _, _ in memory_store.open_followups()]


def test_vector_roundtrip():
    fid = memory_store.add_fact("vec test")
    assert memory_search.unembedded("fact") == [(fid, "vec test")]
    memory_search.store_vectors("fact", [(fid, [0.1, 0.2, 0.3])])
    assert memory_search.unembedded("fact") == []
    (row,) = memory_search.searchable_rows(("fact",))
    assert row.vec is not None and len(row.vec) == 3
    assert row.vec[1] == pytest.approx(0.2)


def test_procedures_searchable():
    memory_store.add_procedure("morning", "briefing in Uzbek")
    rows = memory_search.searchable_rows(("procedure",))
    assert rows[0].text == "briefing in Uzbek"
