"""Semantic memory recall for the voice agent.

Embeds memory chunks — the shared ``data/memories/*.md`` files plus the voice
journal — with DashScope ``text-embedding-v3`` and ranks recall by MEANING, not
just keyword overlap ("what coffee do I drink?" finds "I like dark roast" even
with no shared words). Hybrid score = 0.6·cosine + 0.4·keyword.

Deliberately stdlib-only (sqlite3 + urllib + struct): the corpus is small and
grows slowly, so a tiny sqlite vector table + Python cosine is plenty and adds
no heavy deps (no torch/numpy) to the small VPS. Degrades to keyword-only when
embeddings are unavailable (no key / network), so recall never hard-fails.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import struct
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger("nemo.memory_index")

_DATA_DIR = Path(
    os.environ.get("NEMO_VOICE_DATA_DIR")
    or (Path(__file__).resolve().parents[2] / "data")
)
_MEM_DIR = _DATA_DIR / "memories"
_JOURNAL = _DATA_DIR / "voice_journal.jsonl"
_INDEX_DB = _DATA_DIR / "memory_index.db"

_EMBED_URL = os.environ.get(
    "QWEN_EMBED_URL",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/embeddings",
)
_EMBED_MODEL = os.environ.get("QWEN_EMBED_MODEL", "text-embedding-v3")

_VEC_WEIGHT = 0.6
_KW_WEIGHT = 0.4
_MIN_CHARS = 8
_BATCH = 10
# Embed up to this many new chunks per call. The corpus is small + embedding is
# cheap/batched, so a full first-index (~a few seconds, once) beats partial
# recall. Subsequent calls only embed genuinely new turns.
_MAX_NEW_PER_CALL = 256
_JOURNAL_TAIL = 2000
# Dense embeddings put unrelated short texts around cosine 0.3-0.5, so an
# absolute floor is needed or recall returns confident-looking noise. Related
# pairs sit ~0.6+. Tunable as the corpus grows.
_MIN_COS = float(os.environ.get("QWEN_EMBED_MIN_COS", "0.45"))
# Saved facts (memory/*.md) are curated; rank them above raw journal speech.
_MEMORY_BOOST = 0.1


def _api_key() -> str:
    # Read at call time, not import: the voice server loads .env AFTER importing
    # this module, so a module-level constant would capture an empty key.
    return os.environ.get("DASHSCOPE_API_KEY", "").strip()


def available() -> bool:
    """True if semantic search is usable (an embedding key is configured)."""
    return bool(_api_key())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── embedding ──────────────────────────────────────────────────────────────


def _embed(texts: list[str]) -> list[list[float]] | None:
    """Embed texts via DashScope; None on any failure (caller degrades)."""
    key = _api_key()
    if not key or not texts:
        return None
    body = json.dumps({"model": _EMBED_MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        _EMBED_URL,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
        return [d["embedding"] for d in resp["data"]]
    except Exception as exc:  # noqa: BLE001 — never let recall crash the turn
        LOG.warning("embedding request failed: %s", exc)
        return None


# ── store ──────────────────────────────────────────────────────────────────


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(_INDEX_DB), timeout=5.0)
    # WAL lets the engine and the voice process read/write this shared index
    # concurrently instead of blocking each other ("database is locked"). WAL is
    # persistent at the file level; busy_timeout waits out a transient writer.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute(
        "CREATE TABLE IF NOT EXISTS chunks "
        "(id TEXT PRIMARY KEY, source TEXT, ref TEXT, text TEXT, "
        "vec BLOB, updated_at TEXT)"
    )
    return con


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"{len(blob) // 4}f", blob)


def _chunk_id(source: str, text: str) -> str:
    """Stable id from content — same text never re-embeds; changed text does."""
    return hashlib.sha1(f"{source}:{text}".encode()).hexdigest()[:16]


# ── chunk sources ──────────────────────────────────────────────────────────


def _gather() -> list[tuple[str, str, str]]:
    """Collect (source, ref, text) chunks from memory files + voice journal."""
    out: list[tuple[str, str, str]] = []
    if _MEM_DIR.is_dir():
        for f in sorted(_MEM_DIR.glob("**/*.md")):
            try:
                lines = f.read_text().splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines):
                t = line.strip("-* \t")
                if len(t) >= _MIN_CHARS:
                    out.append(("memory", f"{f.name}:{i}", t[:400]))
    if _JOURNAL.exists():
        out.extend(_journal_chunks())
    return out


def _journal_chunks() -> list[tuple[str, str, str]]:
    """Index only what AVAZBEK said — his own words are the signal for recall.
    Nemo's (often long) replies are conversational noise that pollutes results.
    """
    try:
        lines = _JOURNAL.read_text().splitlines()[-_JOURNAL_TAIL:]
    except OSError:
        return []
    out: list[tuple[str, str, str]] = []
    for i, raw in enumerate(lines):
        try:
            it = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if it.get("role") != "user":
            continue
        t = (it.get("text") or "").strip()
        if len(t) >= _MIN_CHARS:
            out.append(("voice", f"j{i}", f"Avazbek said: {t[:400]}"))
    return out


# reindex() writes (DELETE + INSERT) — during a live conversation new journal
# turns make every recall a writer, and the engine writes the same index, so
# back-to-back recalls collide ("database is locked"). Throttle: reindex at most
# this often; recall tolerates seconds of staleness (recent context comes from
# the rolling history, not semantic search).
_REINDEX_MIN_SEC = float(os.environ.get("NEMO_REINDEX_MIN_SEC", "45"))
_last_reindex = 0.0


def _maybe_reindex() -> None:
    global _last_reindex
    now = time.monotonic()
    if now - _last_reindex < _REINDEX_MIN_SEC:
        return
    _last_reindex = now
    reindex()


def reindex() -> int:
    """Embed chunks new since last run + prune stale ones. Returns # embedded."""
    if not available():
        return 0
    chunks = _gather()
    ids_now = {_chunk_id(s, t): (s, r, t) for s, r, t in chunks}
    con = _connect()
    try:
        have = {row[0] for row in con.execute("SELECT id FROM chunks")}
        stale = have - ids_now.keys()
        if stale:
            con.executemany("DELETE FROM chunks WHERE id = ?", [(i,) for i in stale])
        todo = [(cid, v) for cid, v in ids_now.items() if cid not in have]
        added = _embed_into(con, todo[:_MAX_NEW_PER_CALL])
        con.commit()
        return added
    finally:
        con.close()


def _embed_into(con: sqlite3.Connection, todo: list) -> int:
    """Embed a list of (id, (source, ref, text)) and insert; returns count."""
    added = 0
    for i in range(0, len(todo), _BATCH):
        batch = todo[i : i + _BATCH]
        vecs = _embed([v[2] for _, v in batch])
        if vecs is None:
            break
        con.executemany(
            "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
            [
                (cid, s, r, t, _pack(vec), _now())
                for (cid, (s, r, t)), vec in zip(batch, vecs)
            ],
        )
        added += len(batch)
    return added


# ── search ─────────────────────────────────────────────────────────────────


def _terms(text: str) -> set[str]:
    return {w for w in text.lower().replace(",", " ").split() if len(w) > 2}


def _cosine(a: list[float], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def search(query: str, limit: int = 6) -> list[str]:
    """Top matching memory chunks by meaning (hybrid). [] if unavailable."""
    q = (query or "").strip()
    if not q or not available():
        return []
    _maybe_reindex()
    qv = _embed([q])
    if not qv:
        return []
    qvec = qv[0]
    qterms = _terms(q)
    con = _connect()
    try:
        rows = con.execute("SELECT source, text, vec FROM chunks").fetchall()
    finally:
        con.close()
    scored: list[tuple[float, str]] = []
    for source, text, blob in rows:
        cos = _cosine(qvec, _unpack(blob))
        if cos < _MIN_COS:  # drop unrelated matches (dense-embedding baseline)
            continue
        kw = _keyword_score(qterms, text)
        # Curated facts (saved memory files) outrank raw conversation lines.
        boost = _MEMORY_BOOST if source == "memory" else 0.0
        scored.append((_VEC_WEIGHT * cos + _KW_WEIGHT * kw + boost, text))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in scored[:limit]]


def _keyword_score(qterms: set[str], text: str) -> float:
    if not qterms:
        return 0.0
    hits = len(qterms & _terms(text))
    return hits / len(qterms)
