"""Voice lock — only Avazbek's voice unlocks the sensitive side of Nemo.

Every spoken turn's audio is verified against his enrolled voiceprint,
IN PARALLEL with the reply Qwen is already generating — so verification adds
zero perceived latency. Verdicts:

* OWNER    — cosine ≥ OWNER_COS: full access.
* UNSURE   — gray zone (sick voice, noisy room): Nemo asks the phone for a
  biometric check (Face ID / fingerprint via BiometricPrompt) — a second
  factor we don't have to build ML for. One OK verifies the whole session.
* STRANGER — below the floor: chit-chat only; every protected tool (messages,
  memory, phone control, delegation) hard-refuses regardless of what the
  model tries to call. Fails closed.

Embedders (SPEAKER_EMBEDDER):
* ``resemblyzer`` (default) — real speaker d-vectors; needs `pip install
  resemblyzer` on the VPS. Missing → gate reports OFF and everything works
  as before (flag-off behavior).
* ``energy`` — 16-bin amplitude histogram; deterministic, dependency-free.
  For tests and the local harness ONLY, not real security.

Enroll: `python speaker_gate.py enroll sample.wav` (10-30s of his speech).
Enable: VOICE_SPEAKER_LOCK=1.
"""

from __future__ import annotations

import json
import logging
import math
import os
import struct
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

LOG = logging.getLogger("nemo.speaker_gate")

_OWNER_COS = float(os.environ.get("SPEAKER_OWNER_COS", "0.82"))
_UNSURE_COS = float(os.environ.get("SPEAKER_UNSURE_COS", "0.60"))
_GUEST_COS = float(os.environ.get("SPEAKER_GUEST_COS", "0.80"))
_MIN_PCM_BYTES = 16000 * 2 // 2  # ≥0.5s at 16k mono — shorter can't verify
_SAMPLE_RATE = 16000


class Verdict(str, Enum):
    OWNER = "owner"
    UNSURE = "unsure"
    STRANGER = "stranger"
    OFF = "off"  # gate disabled / not enrolled / embedder missing


def enabled() -> bool:
    return os.environ.get("VOICE_SPEAKER_LOCK", "0").strip() == "1"


def _profile_path() -> Path:
    base = Path(
        os.environ.get("NEMO_VOICE_DATA_DIR")
        or (Path(__file__).resolve().parents[2] / "data")
    )
    return base / "voiceprint.json"


# ── embedders ───────────────────────────────────────────────────────────────


def _embed_energy(pcm: bytes) -> list[float]:
    """Amplitude-histogram embedding over VOICED samples only (silence would
    dominate the histogram and wash out the speaker) — deterministic test
    double, not real security."""
    n = len(pcm) // 2
    samples = struct.unpack(f"{n}h", pcm[: n * 2])
    bins = [0.0] * 16
    for s in samples:
        if abs(s) >= 300:
            bins[min(15, abs(s) // 2048)] += 1.0
    total = sum(bins) or 1.0
    return [b / total for b in bins]


def _embed_resemblyzer(pcm: bytes) -> list[float] | None:
    try:
        import numpy as np  # type: ignore[import-not-found]
        from resemblyzer import VoiceEncoder  # type: ignore[import-not-found]
    except ImportError:
        LOG.warning("resemblyzer not installed — speaker gate OFF")
        return None
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = VoiceEncoder()
    wav = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return [float(x) for x in _ENCODER.embed_utterance(wav)]


_ENCODER = None


def _embed(pcm: bytes) -> list[float] | None:
    mode = os.environ.get("SPEAKER_EMBEDDER", "resemblyzer").strip()
    if mode == "energy":
        return _embed_energy(pcm)
    return _embed_resemblyzer(pcm)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ── enrollment + verification ───────────────────────────────────────────────


def _load_all() -> dict:
    try:
        return json.loads(_profile_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_all(data: dict) -> None:
    path = _profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def enroll(pcm: bytes) -> bool:
    vec = _embed(pcm)
    if vec is None:
        return False
    data = _load_all()
    data["owner"] = vec
    _save_all(data)
    LOG.info("owner voiceprint enrolled (%d dims)", len(vec))
    return True


def enroll_guest(name: str, pcm: bytes) -> bool:
    """Named guest voiceprint — attribution only, never access."""
    vec = _embed(pcm)
    if vec is None or len(pcm) < _MIN_PCM_BYTES:
        return False
    data = _load_all()
    data.setdefault("guests", {})[name[:40]] = vec
    _save_all(data)
    LOG.info("guest voiceprint enrolled: %s", name)
    return True


def _load_profile() -> list[float] | None:
    owner = _load_all().get("owner")
    return owner if isinstance(owner, list) else None


def _match_guest(vec: list[float]) -> str | None:
    guests = _load_all().get("guests") or {}
    best, best_score = None, 0.0
    for name, gvec in guests.items():
        score = _cosine(vec, gvec)
        if score > best_score:
            best, best_score = name, score
    return best if best_score >= _GUEST_COS else None


def verify(pcm: bytes) -> Verdict:
    """Blocking (~100-300ms with resemblyzer) — call via asyncio.to_thread."""
    return identify(pcm)[0]


def identify(pcm: bytes) -> tuple[Verdict, str | None]:
    """(verdict, speaker_name). Name is set for the owner ('Avazbek') and for
    enrolled guests; guests get attribution in memory, never tool access."""
    if not enabled():
        return Verdict.OFF, None
    if len(pcm) < _MIN_PCM_BYTES:
        return Verdict.UNSURE, None  # too short — second factor decides
    profile = _load_profile()
    if profile is None:
        LOG.warning("speaker lock on but no voiceprint enrolled — gate OFF")
        return Verdict.OFF, None
    vec = _embed(pcm)
    if vec is None:
        return Verdict.OFF, None
    score = _cosine(vec, profile)
    if score >= _OWNER_COS:
        return Verdict.OWNER, "Avazbek"
    if score >= _UNSURE_COS:
        return Verdict.UNSURE, None
    return Verdict.STRANGER, _match_guest(vec)


# ── per-session state ───────────────────────────────────────────────────────


@dataclass
class SpeakerState:
    """One voice session's identity state. Owned by _SessionCtx."""

    pcm_buf: bytearray = field(default_factory=bytearray)
    last_verdict: Verdict = Verdict.OFF
    name: str | None = None  # who spoke last (owner or enrolled guest)
    pending_enroll: str | None = None  # next non-owner turn enrolls this name
    session_verified: bool = False  # set by a passed phone-biometric check
    biometric_pending: bool = False

    def take_turn_audio(self) -> bytes:
        pcm = bytes(self.pcm_buf)
        self.pcm_buf.clear()
        return pcm

    def record(self, verdict: Verdict, name: str | None = None) -> None:
        self.last_verdict = verdict
        self.name = name

    def allow_sensitive(self) -> bool:
        """May the CURRENT speaker touch protected tools? Fails closed.
        A STRANGER verdict always blocks. UNSURE (gray zone: sick voice, noise)
        is allowed only after an actual phone-biometric pass set
        ``session_verified`` — NOT merely because the owner spoke earlier, or a
        gray-zone guest handed the phone after an owner turn would inherit
        access. An OWNER verdict is allowed on its own; it does not need or set
        session_verified."""
        if self.last_verdict in (Verdict.OFF, Verdict.OWNER):
            return True
        if self.last_verdict is Verdict.UNSURE:
            return self.session_verified
        return False


def _enroll_cli(wav_path: str) -> int:
    import wave

    with wave.open(wav_path, "rb") as w:
        if w.getframerate() != _SAMPLE_RATE or w.getnchannels() != 1:
            print(f"need {_SAMPLE_RATE}Hz mono wav")
            return 1
        pcm = w.readframes(w.getnframes())
    return 0 if enroll(pcm) else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "enroll":
        raise SystemExit(_enroll_cli(sys.argv[2]))
    print("usage: python speaker_gate.py enroll <16k-mono.wav>")
    raise SystemExit(1)
