"""Energy-based voice activity detection for the omni gateway.

DashScope's realtime API does server-side VAD; self-hosted, we do it here.
Simple RMS-energy endpointing over PCM16 frames: speech starts when energy
crosses the threshold for a few consecutive frames, ends after a silence
window. No ML dependency — good enough for a quiet-room single speaker, and
swappable for silero-vad later behind the same two-method interface.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field


@dataclass
class VadConfig:
    sample_rate: int = 16000
    frame_ms: int = 20
    energy_threshold: float = 500.0  # RMS over int16 samples
    start_frames: int = 3  # consecutive voiced frames → speech_started
    end_ms: int = 700  # trailing silence → speech_ended


@dataclass
class EnergyVad:
    """Feed PCM16 bytes; poll events. States: idle → speaking → idle."""

    cfg: VadConfig = field(default_factory=VadConfig)
    _speaking: bool = False
    _voiced_run: int = 0
    _silence_ms: float = 0.0
    _residual: bytes = b""

    @property
    def speaking(self) -> bool:
        return self._speaking

    def _frame_bytes(self) -> int:
        return self.cfg.sample_rate * 2 * self.cfg.frame_ms // 1000

    def feed(self, pcm: bytes) -> list[str]:
        """Consume audio; return events: 'speech_started' / 'speech_ended'."""
        events: list[str] = []
        data = self._residual + pcm
        size = self._frame_bytes()
        offset = 0
        while offset + size <= len(data):
            events.extend(self._frame(data[offset : offset + size]))
            offset += size
        self._residual = data[offset:]
        return events

    def _frame(self, frame: bytes) -> list[str]:
        voiced = _rms(frame) >= self.cfg.energy_threshold
        if not self._speaking:
            return self._idle_frame(voiced)
        return self._speaking_frame(voiced)

    def _idle_frame(self, voiced: bool) -> list[str]:
        self._voiced_run = self._voiced_run + 1 if voiced else 0
        if self._voiced_run >= self.cfg.start_frames:
            self._speaking = True
            self._silence_ms = 0.0
            return ["speech_started"]
        return []

    def _speaking_frame(self, voiced: bool) -> list[str]:
        if voiced:
            self._silence_ms = 0.0
            return []
        self._silence_ms += self.cfg.frame_ms
        if self._silence_ms >= self.cfg.end_ms:
            self._speaking = False
            self._voiced_run = 0
            return ["speech_ended"]
        return []

    def reset(self) -> None:
        self._speaking = False
        self._voiced_run = 0
        self._silence_ms = 0.0
        self._residual = b""


def _rms(frame: bytes) -> float:
    n = len(frame) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"{n}h", frame[: n * 2])
    return math.sqrt(sum(s * s for s in samples) / n)
