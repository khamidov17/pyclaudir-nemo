"""EnergyVad — endpointing state machine over synthetic PCM."""

from __future__ import annotations

import struct

from vad import EnergyVad, VadConfig


def _pcm(val: int, ms: int, rate: int = 16000) -> bytes:
    n = rate * ms // 1000
    return struct.pack(f"{n}h", *([val] * n))


def _vad() -> EnergyVad:
    return EnergyVad(cfg=VadConfig(start_frames=2, end_ms=60))


def test_speech_start_needs_consecutive_voiced_frames():
    vad = _vad()
    assert vad.feed(_pcm(3000, 20)) == []  # one voiced frame: not yet
    assert vad.feed(_pcm(3000, 20)) == ["speech_started"]
    assert vad.speaking


def test_single_spike_does_not_trigger():
    vad = _vad()
    vad.feed(_pcm(3000, 20))
    assert vad.feed(_pcm(0, 200)) == []
    assert not vad.speaking


def test_speech_end_after_silence_window():
    vad = _vad()
    vad.feed(_pcm(3000, 40))
    assert vad.speaking
    assert vad.feed(_pcm(0, 40)) == []  # 40ms silence < 60ms window
    assert vad.feed(_pcm(0, 20)) == ["speech_ended"]
    assert not vad.speaking


def test_brief_pause_does_not_end_turn():
    vad = _vad()
    vad.feed(_pcm(3000, 40))
    vad.feed(_pcm(0, 40))  # pause shorter than end_ms
    vad.feed(_pcm(3000, 20))  # resumes speaking
    assert vad.feed(_pcm(0, 40)) == []
    assert vad.speaking


def test_partial_frames_buffered():
    vad = _vad()
    half = _pcm(3000, 10)
    assert vad.feed(half) == []  # half a frame: buffered, no decision
    vad.feed(half + _pcm(3000, 20))
    assert vad.speaking


def test_reset():
    vad = _vad()
    vad.feed(_pcm(3000, 40))
    vad.reset()
    assert not vad.speaking
    assert vad.feed(_pcm(3000, 20)) == []  # run counter cleared
