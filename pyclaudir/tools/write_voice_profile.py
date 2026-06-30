"""write_voice_profile — persist a JSON voice persona chunk to memory_index.db.

Called by the daily profile synthesis reminder so Nemo's persona injection has
fresh data. The voice server reads this chunk at session start (VOICE_PERSONA=1).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from .base import BaseTool, ToolResult

# Match the voice server's NEMO_VOICE_DATA_DIR so both sides write to the same DB.
# Fallback chain: NEMO_VOICE_DATA_DIR → PYCLAUDIR_DATA_DIR → ./data
_DATA_DIR = Path(
    os.environ.get("NEMO_VOICE_DATA_DIR")
    or os.environ.get("PYCLAUDIR_DATA_DIR")
    or "./data"
).resolve()
_INDEX_DB = _DATA_DIR / "memory_index.db"
_DDL = (
    "CREATE TABLE IF NOT EXISTS chunks "
    "(id TEXT PRIMARY KEY, source TEXT, ref TEXT, text TEXT, vec BLOB, updated_at TEXT)"
)
_CHUNK_ID = hashlib.sha1(b"voice_profile").hexdigest()[:16]

_log = logging.getLogger("pyclaudir.write_voice_profile")


class WriteVoiceProfileArgs(BaseModel):
    name: str = Field(description="The user's preferred name.")
    tone: str = Field(description="Preferred communication tone in 1-2 sentences.")
    topics: list[str] = Field(
        default_factory=list,
        description="Up to 5 current topics of interest.",
    )


class WriteVoiceProfileTool(BaseTool):
    name = "write_voice_profile"
    description = (
        "Write or update the voice persona profile used for session injection. "
        "Call this after refreshing ABOUT_ME.md so Nemo introduces itself correctly. "
        "Fields: preferred name, communication tone, and up to 5 current topics."
    )
    args_model = WriteVoiceProfileArgs

    async def run(self, args: WriteVoiceProfileArgs) -> ToolResult:
        name = args.name.strip()[:100]
        tone = args.tone.strip()[:300]
        topics = [t.strip()[:80] for t in args.topics[:5]]
        if not name:
            return ToolResult(content="name is required", is_error=True)
        profile = {"name": name, "tone": tone, "topics": topics}
        text = json.dumps(profile)
        ts = datetime.now(timezone.utc).isoformat()
        try:
            con = sqlite3.connect(str(_INDEX_DB), timeout=5.0)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=5000")
            con.execute(_DDL)
            con.execute(
                "INSERT OR REPLACE INTO chunks "
                "(id, source, ref, text, vec, updated_at) VALUES (?, 'voice_profile', "
                "'voice_profile', ?, NULL, ?)",
                (_CHUNK_ID, text, ts),
            )
            con.commit()
            con.close()
            _log.info("voice profile written: name=%s topics=%d", name, len(topics))
            return ToolResult(
                content=f"Voice profile saved: name={name!r}, topics={len(topics)}"
            )
        except Exception as exc:  # noqa: BLE001
            _log.error("write_voice_profile failed: %s", exc)
            return ToolResult(content=f"error: {exc}", is_error=True)
