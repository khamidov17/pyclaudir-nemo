"""memory_migrate — legacy import, idempotence, malformed input tolerance."""

from __future__ import annotations

import json

import pytest

import memory_migrate
import memory_store


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    return tmp_path


def _seed_legacy(tmp_path):
    mem = tmp_path / "memories"
    mem.mkdir()
    (mem / "voice_facts.md").write_text(
        "# heading\n- Avazbek likes dark roast\n- short\n- Works on the Nemo assistant\n"
    )
    journal = tmp_path / "voice_journal.jsonl"
    journal.write_text(
        json.dumps({"role": "user", "text": "remind me about the clinic"})
        + "\n"
        + json.dumps({"role": "assistant", "text": "will do"})
        + "\nnot-json\n"
    )


def test_migrates_facts_and_user_episodes(_data_dir):
    _seed_legacy(_data_dir)
    counts = memory_migrate.migrate_legacy()
    assert counts == {"facts": 2, "episodes": 1}
    texts = [t for _, _, t in memory_store.live_facts()]
    assert "Avazbek likes dark roast" in texts
    episodes = memory_store.episodes_since(0)
    assert episodes[0][1] == "user"
    assert "clinic" in episodes[0][2]


def test_idempotent(_data_dir):
    _seed_legacy(_data_dir)
    memory_migrate.migrate_legacy()
    assert memory_migrate.migrated()
    assert memory_migrate.migrate_legacy() == {}
    assert len(memory_store.live_facts()) == 2


def test_no_legacy_data_is_fine(_data_dir):
    counts = memory_migrate.migrate_legacy()
    assert counts == {"facts": 0, "episodes": 0}
    assert memory_migrate.migrated()
