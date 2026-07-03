"""Meeting-recording store: dated audio + transcripts on shared disk.

The phone records a whole-room meeting and uploads the audio (POST
``/recording/upload``); the engine transcribes it (Groq Whisper, ``stt.py``) and
keeps a small dated index. Voice recall ("summarize what we recorded", "send me
the transcript") reaches these through the engine brain, which reads the
transcript files here.

Storage is bounded: oldest recordings are evicted once the total exceeds a size
or count cap, so a long meeting can't grow the disk without limit.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# A recording id is used as a single path segment (``<root>/<id>/``) and comes
# from the upload form, so it is attacker-influenced. Restrict it to a safe
# charset and forbid the directory-traversal names so it can never escape root.
_SAFE_REC_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def is_safe_rec_id(rec_id: str) -> bool:
    """True if ``rec_id`` is safe to use as a path segment (no traversal)."""
    return (
        bool(rec_id) and rec_id not in (".", "..") and bool(_SAFE_REC_ID.match(rec_id))
    )


log = logging.getLogger("pyclaudir.recording_store")

_MAX_RECORDINGS = 50
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB across all recordings


@dataclass(frozen=True)
class SaveOpts:
    """Upload metadata for a new recording (keeps save_audio's arity small)."""

    audio_name: str
    started_ms: int
    ended_ms: int


@dataclass(frozen=True)
class RecordingMeta:
    """One recording's metadata (mirrors the on-disk ``meta.json``)."""

    id: str
    started_ms: int
    ended_ms: int
    audio_name: str
    audio_bytes: int
    title: str = ""
    transcribed: bool = False

    @property
    def duration_sec(self) -> int:
        return max(0, (self.ended_ms - self.started_ms) // 1000)

    @property
    def date(self) -> str:
        """Local-ish date label (UTC) for "recordings from June 22"."""
        return time.strftime("%Y-%m-%d", time.gmtime(self.started_ms / 1000))


class RecordingStore:
    """Dated recordings under ``<root>/<id>/`` = audio + transcript.txt + meta.json."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _dir(self, rec_id: str) -> Path:
        # Guard every path built from a rec_id (save, read, transcript, get) at
        # this single choke point — reject anything that could escape the root.
        if not is_safe_rec_id(rec_id):
            raise ValueError(f"invalid recording id: {rec_id!r}")
        return self._root / rec_id

    def save_audio(self, rec_id: str, audio: bytes, opts: "SaveOpts") -> RecordingMeta:
        """Persist uploaded audio + initial meta (transcript pending)."""
        d = self._dir(rec_id)
        d.mkdir(parents=True, exist_ok=True)
        safe = Path(opts.audio_name).name or "audio.m4a"
        (d / safe).write_bytes(audio)
        meta = RecordingMeta(
            id=rec_id,
            started_ms=opts.started_ms,
            ended_ms=opts.ended_ms,
            audio_name=safe,
            audio_bytes=len(audio),
        )
        self._write_meta(meta)
        self._evict()
        return meta

    def audio_path(self, rec_id: str) -> Path | None:
        meta = self.get(rec_id)
        if meta is None:
            return None
        p = self._dir(rec_id) / meta.audio_name
        return p if p.exists() else None

    def set_transcript(self, rec_id: str, text: str) -> None:
        d = self._dir(rec_id)
        if not d.exists():
            return
        (d / "transcript.txt").write_text(text, encoding="utf-8")
        meta = self.get(rec_id)
        if meta is not None:
            self._write_meta(RecordingMeta(**{**asdict(meta), "transcribed": True}))

    def read_transcript(self, rec_id: str) -> str | None:
        p = self._dir(rec_id) / "transcript.txt"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def get(self, rec_id: str) -> RecordingMeta | None:
        p = self._dir(rec_id) / "meta.json"
        if not p.exists():
            return None
        try:
            return RecordingMeta(**json.loads(p.read_text()))
        except Exception:
            return None

    def list(self) -> list[RecordingMeta]:
        """All recordings, newest first."""
        metas = [
            m for d in self._root.iterdir() if d.is_dir() if (m := self.get(d.name))
        ]
        return sorted(metas, key=lambda m: m.started_ms, reverse=True)

    def latest(self) -> RecordingMeta | None:
        items = self.list()
        return items[0] if items else None

    def _write_meta(self, meta: RecordingMeta) -> None:
        (self._dir(meta.id) / "meta.json").write_text(json.dumps(asdict(meta)))

    def _evict(self) -> None:
        """Drop oldest recordings past the count or total-size cap."""
        items = sorted(self.list(), key=lambda m: m.started_ms)  # oldest first
        total = sum(m.audio_bytes for m in items)
        while items and (len(items) > _MAX_RECORDINGS or total > _MAX_TOTAL_BYTES):
            victim = items.pop(0)
            total -= victim.audio_bytes
            self._remove(victim.id)
            log.info("evicted recording %s (%d bytes)", victim.id, victim.audio_bytes)

    def _remove(self, rec_id: str) -> None:
        import shutil

        shutil.rmtree(self._dir(rec_id), ignore_errors=True)
