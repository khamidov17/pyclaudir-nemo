"""speaker_gate — verdict bands, state machine, enrollment, fail-closed."""

from __future__ import annotations

import struct

import pytest

import speaker_gate
from speaker_gate import SpeakerState, Verdict


def _pcm(val: int, ms: int = 800, rate: int = 16000) -> bytes:
    n = rate * ms // 1000
    return struct.pack(f"{n}h", *([val] * n))


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_SPEAKER_LOCK", "1")
    monkeypatch.setenv("SPEAKER_EMBEDDER", "energy")


def test_disabled_returns_off(monkeypatch):
    monkeypatch.setenv("VOICE_SPEAKER_LOCK", "0")
    assert speaker_gate.verify(_pcm(3000)) is Verdict.OFF


def test_no_enrollment_returns_off():
    assert speaker_gate.verify(_pcm(3000)) is Verdict.OFF


def test_owner_voice_matches():
    assert speaker_gate.enroll(_pcm(3000))
    assert speaker_gate.verify(_pcm(3000)) is Verdict.OWNER


def test_different_voice_is_stranger():
    speaker_gate.enroll(_pcm(3000))
    # Completely different amplitude profile → different histogram bins.
    assert speaker_gate.verify(_pcm(31000)) is Verdict.STRANGER


def test_short_audio_is_unsure():
    speaker_gate.enroll(_pcm(3000))
    assert speaker_gate.verify(_pcm(3000, ms=200)) is Verdict.UNSURE


# ── SpeakerState ────────────────────────────────────────────────────────────


def test_stranger_blocked_even_in_verified_session():
    s = SpeakerState()
    s.record(Verdict.OWNER)
    assert s.allow_sensitive()
    s.record(Verdict.STRANGER)  # phone handed to someone else mid-session
    assert not s.allow_sensitive()


def test_owner_speech_does_not_verify_unsure():
    """An UNSURE voice after an OWNER turn must NOT inherit access — the owner
    speaking is not a biometric pass (a gray-zone guest handoff would else get
    in)."""
    s = SpeakerState()
    s.record(Verdict.OWNER)
    assert not s.session_verified
    s.record(Verdict.UNSURE)  # gray-zone voice (cousin / just-enrolled guest)
    assert not s.allow_sensitive()
    s.session_verified = True  # phone biometric actually passed
    s.record(Verdict.UNSURE)
    assert s.allow_sensitive()


def test_stranger_blocked_off_allowed():
    s = SpeakerState()
    s.record(Verdict.STRANGER)
    assert not s.allow_sensitive()
    s2 = SpeakerState()
    s2.record(Verdict.OFF)
    assert s2.allow_sensitive()


def test_unsure_blocked_until_biometric():
    s = SpeakerState()
    s.record(Verdict.UNSURE)
    assert not s.allow_sensitive()
    s.session_verified = True  # phone Face ID passed
    assert s.allow_sensitive()


def test_turn_audio_buffer_drains():
    s = SpeakerState()
    s.pcm_buf.extend(b"\x01\x02")
    assert s.take_turn_audio() == b"\x01\x02"
    assert s.take_turn_audio() == b""


def test_owner_write_tools_are_protected():
    """Every tool that writes owner data or controls the phone must be gated —
    a stranger's voice must not reach them."""
    from pump_tools import _PROTECTED_TOOLS

    must_protect = {
        "remember",
        "recall",
        "send_telegram",
        "delegate_task",
        "log_expense",
        "log_habit",
        "ledger_summary",
        "add_flashcard",
        "quiz_me",
        "grade_card",
        "scan",
        "enroll_speaker",
        "set_reminder",
        "list_reminders",
        "cancel_reminder",
        "read_messages",
    }
    assert must_protect <= _PROTECTED_TOOLS
