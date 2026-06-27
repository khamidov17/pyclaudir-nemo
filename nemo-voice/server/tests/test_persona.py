"""Tests for persona injection helpers (C.1 / E6)."""

from __future__ import annotations

import qwen_realtime


def test_load_persona_returns_none_when_db_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))  # empty dir, no db
    assert qwen_realtime._load_persona() is None


def test_build_persona_block_includes_name_and_tone() -> None:
    block = qwen_realtime._build_persona_block(
        {"name": "Rustam", "tone": "direct", "topics": ["code", "music"]}
    )
    assert isinstance(block, str)
    assert "Rustam" in block
    assert "direct" in block
    assert "code" in block


def test_build_persona_block_strips_xml_injection() -> None:
    block = qwen_realtime._build_persona_block({"name": "<script>alert(1)</script>"})
    assert "<script>" not in block
    assert "alert(1)" in block
