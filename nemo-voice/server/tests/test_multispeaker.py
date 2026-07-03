"""Multi-speaker memory — guest enrollment, identification, attribution."""

from __future__ import annotations

import struct

import pytest

import memory_store
import speaker_gate
import voice_history
from speaker_gate import Verdict


def _pcm(val: int, ms: int = 800, rate: int = 16000) -> bytes:
    n = rate * ms // 1000
    return struct.pack(f"{n}h", *([val] * n))


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_SPEAKER_LOCK", "1")
    monkeypatch.setenv("SPEAKER_EMBEDDER", "energy")
    monkeypatch.setenv("VOICE_MEMORY_V2", "1")
    monkeypatch.setattr(voice_history, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(voice_history, "_RECENT", tmp_path / "voice_recent.json")
    monkeypatch.setattr(voice_history, "_JOURNAL", tmp_path / "voice_journal.jsonl")
    speaker_gate.enroll(_pcm(3000))  # owner


def test_identify_owner_guest_stranger():
    assert speaker_gate.enroll_guest("Aziz", _pcm(31000))
    assert speaker_gate.identify(_pcm(3000)) == (Verdict.OWNER, "Avazbek")
    assert speaker_gate.identify(_pcm(31000)) == (Verdict.STRANGER, "Aziz")
    assert speaker_gate.identify(_pcm(12000)) == (Verdict.STRANGER, None)


def test_guest_enrollment_needs_enough_audio():
    assert not speaker_gate.enroll_guest("Aziz", _pcm(31000, ms=200))


def test_guest_never_gets_sensitive_access():
    speaker_gate.enroll_guest("Aziz", _pcm(31000))
    state = speaker_gate.SpeakerState()
    verdict, name = speaker_gate.identify(_pcm(31000))
    state.record(verdict, name)
    assert state.name == "Aziz"
    assert not state.allow_sensitive()  # attribution ≠ access


def test_attributed_journaling():
    voice_history.add("user", "kechqurun futbol bor", speaker="Aziz")
    voice_history.add("user", "men boraman", speaker="Avazbek")
    rows = memory_store.episodes_since(0)
    assert rows[0][2] == "Aziz: kechqurun futbol bor"  # name woven for recall
    assert rows[1][2] == "men boraman"  # owner text stays raw
    con = memory_store.connect()
    speakers = [s for (s,) in con.execute("SELECT speaker FROM episodes ORDER BY id")]
    con.close()
    assert speakers == ["Aziz", "Avazbek"]


def test_owner_enrollment_untouched_by_guests():
    speaker_gate.enroll_guest("Aziz", _pcm(31000))
    assert speaker_gate.identify(_pcm(3000)) == (Verdict.OWNER, "Avazbek")
