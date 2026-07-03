"""Memory v2 store — episodic / semantic / procedural memory + follow-ups.

One SQLite DB (``data/memory_v2.db``) with four tables:

* ``episodes``   — what happened (journal turns, one row per utterance)
* ``facts``      — durable facts about Avazbek/world; upserted, never blindly
                   appended; contradicted facts are superseded, not deleted
* ``procedures`` — how he likes things done ("when X, do Y")
* ``followups``  — commitments extracted from conversation, with due times

Vectors are struct-packed float32 blobs searched with Python cosine — the same
deliberate stdlib-only choice as memory_index.py: single-user corpus, small
VPS, no compiled extensions. Swap point for sqlite-vec is `knn()` in
memory_search.py if the corpus ever outgrows this.

Writes are cheap and synchronous; callers on the voice path wrap reads in
``asyncio.to_thread`` (see recall.py). Embeddings are filled lazily in batches
(``embed_pending``) so adding an episode never blocks a turn on HTTP.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger("nemo.memory_store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    speaker TEXT NOT NULL DEFAULT '',
    vec BLOB
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.8,
    source TEXT NOT NULL DEFAULT '',
    created_ts TEXT NOT NULL,
    updated_ts TEXT NOT NULL,
    superseded_by INTEGER,
    vec BLOB
);
CREATE TABLE IF NOT EXISTS procedures (
    id INTEGER PRIMARY KEY,
    trigger TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    updated_ts TEXT NOT NULL,
    vec BLOB
);
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_ts TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    source_episode_id INTEGER,
    created_ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_facts_live ON facts(superseded_by);
CREATE INDEX IF NOT EXISTS idx_followups_status ON followups(status);
"""


def memory_v2_enabled() -> bool:
    """Feature flag, read at call time so tests and .env loading both work."""
    return os.environ.get("VOICE_MEMORY_V2", "0").strip() == "1"


def db_path() -> Path:
    """Resolved at call time so tests can point NEMO_VOICE_DATA_DIR elsewhere."""
    base = Path(
        os.environ.get("NEMO_VOICE_DATA_DIR")
        or (Path(__file__).resolve().parents[2] / "data")
    )
    return base / "memory_v2.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(_SCHEMA)
    # Lightweight migration: DBs created before multi-speaker memory lack the
    # speaker column (CREATE IF NOT EXISTS won't add it).
    cols = {c[1] for c in con.execute("PRAGMA table_info(episodes)")}
    if "speaker" not in cols:
        con.execute("ALTER TABLE episodes ADD COLUMN speaker TEXT NOT NULL DEFAULT ''")
    return con


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"{len(blob) // 4}f", blob)


# ── episodes ────────────────────────────────────────────────────────────────


def add_episode(session_id: str, role: str, text: str, speaker: str = "") -> int:
    """Record one utterance. vec stays NULL; embed_pending() fills it later.
    ``speaker`` attributes multi-speaker turns (meetings, ambient guests)."""
    text = (text or "").strip()
    if not text:
        return 0
    con = connect()
    try:
        cur = con.execute(
            "INSERT INTO episodes (session_id, ts, role, text, speaker)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, _now(), role, text[:1000], speaker[:40]),
        )
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def episodes_since(episode_id: int, limit: int = 400) -> list[tuple[int, str, str]]:
    """(id, role, text) rows newer than episode_id — the extractor's feed."""
    con = connect()
    try:
        rows = con.execute(
            "SELECT id, role, text FROM episodes WHERE id > ? ORDER BY id LIMIT ?",
            (episode_id, limit),
        ).fetchall()
        return [(int(i), str(r), str(t)) for i, r, t in rows]
    finally:
        con.close()


# ── facts ───────────────────────────────────────────────────────────────────


def add_fact(
    text: str, subject: str = "", confidence: float = 0.8, source: str = ""
) -> int:
    con = connect()
    try:
        now = _now()
        cur = con.execute(
            "INSERT INTO facts (subject, text, confidence, source, created_ts,"
            " updated_ts) VALUES (?, ?, ?, ?, ?, ?)",
            (subject[:80], text[:500], confidence, source[:80], now, now),
        )
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def supersede_fact(old_id: int, new_id: int) -> None:
    """Mark old_id as replaced by new_id — contradicted facts die, not vanish."""
    con = connect()
    try:
        con.execute(
            "UPDATE facts SET superseded_by = ?, updated_ts = ? WHERE id = ?",
            (new_id, _now(), old_id),
        )
        con.commit()
    finally:
        con.close()


def live_facts(limit: int = 200) -> list[tuple[int, str, str]]:
    """(id, subject, text) of non-superseded facts, newest first."""
    con = connect()
    try:
        rows = con.execute(
            "SELECT id, subject, text FROM facts WHERE superseded_by IS NULL "
            "ORDER BY updated_ts DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(int(i), str(s), str(t)) for i, s, t in rows]
    finally:
        con.close()


# ── procedures ──────────────────────────────────────────────────────────────


def add_procedure(trigger: str, text: str) -> int:
    con = connect()
    try:
        cur = con.execute(
            "INSERT INTO procedures (trigger, text, updated_ts) VALUES (?, ?, ?)",
            (trigger[:120], text[:500], _now()),
        )
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


# ── followups ───────────────────────────────────────────────────────────────


def add_followup(text: str, due_ts: str | None, source_episode_id: int = 0) -> int:
    con = connect()
    try:
        cur = con.execute(
            "INSERT INTO followups (text, due_ts, source_episode_id, created_ts)"
            " VALUES (?, ?, ?, ?)",
            (text[:300], due_ts, source_episode_id or None, _now()),
        )
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def open_followups(limit: int = 50) -> list[tuple[int, str, str | None]]:
    con = connect()
    try:
        rows = con.execute(
            "SELECT id, text, due_ts FROM followups WHERE status = 'open' "
            "ORDER BY due_ts IS NULL, due_ts LIMIT ?",
            (limit,),
        ).fetchall()
        return [(int(i), str(t), d) for i, t, d in rows]
    finally:
        con.close()


def resolve_followup(followup_id: int, status: str = "done") -> None:
    con = connect()
    try:
        con.execute(
            "UPDATE followups SET status = ? WHERE id = ?", (status, followup_id)
        )
        con.commit()
    finally:
        con.close()
