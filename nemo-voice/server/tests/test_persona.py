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


def test_load_persona_returns_voice_profile_chunk(monkeypatch, tmp_path) -> None:
    """voice_profile source chunk is returned as a dict."""
    import json
    import sqlite3

    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    db = tmp_path / "memory_index.db"
    profile = {"name": "Ava", "tone": "calm and direct", "topics": ["voice", "AI"]}
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE chunks (id TEXT PRIMARY KEY, source TEXT, ref TEXT, "
        "text TEXT, vec BLOB, updated_at TEXT)"
    )
    con.execute(
        "INSERT INTO chunks VALUES ('vp01', 'voice_profile', 'voice_profile', ?, NULL, '2024-01-01')",
        (json.dumps(profile),),
    )
    con.commit()
    con.close()
    import importlib
    import qwen_realtime as qr

    importlib.reload(qr)
    result = qr._load_persona()
    assert result == profile


def test_load_persona_returns_none_when_no_voice_profile_row(
    monkeypatch, tmp_path
) -> None:
    """DB exists but has no voice_profile row → returns None (not a memory chunk)."""
    import json
    import sqlite3

    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    db = tmp_path / "memory_index.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE chunks (id TEXT PRIMARY KEY, source TEXT, ref TEXT, "
        "text TEXT, vec BLOB, updated_at TEXT)"
    )
    # A 'memory' row with profile-like JSON — must NOT be picked up
    con.execute(
        "INSERT INTO chunks VALUES ('m01', 'memory', '', ?, NULL, '2024-01-01')",
        (json.dumps({"name": "ghost", "tone": "quiet"}),),
    )
    con.commit()
    con.close()
    import importlib
    import qwen_realtime as qr

    importlib.reload(qr)
    assert qr._load_persona() is None
