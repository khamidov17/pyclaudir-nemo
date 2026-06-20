"""Engine-side semantic memory search — recall by meaning, not just keywords.

Shares the vector index (``data/memory_index.db``) and chunk-id scheme with the
voice agent's ``memory_index`` module, so both brains read one interoperable
store. Embeds with DashScope ``text-embedding-v3`` (key from the engine env),
indexes ``data/memories/*.md``, and ranks with hybrid cosine+keyword. Stdlib
only; degrades to no-op when embeddings are unavailable (caller falls back to
the existing keyword search).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import struct
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("pyclaudir.semantic_memory")

_DATA_DIR = Path(os.environ.get("PYCLAUDIR_DATA_DIR", "./data")).resolve()
_MEM_DIR = _DATA_DIR / "memories"
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
_MAX_NEW = 256
_MIN_COS = float(os.environ.get("QWEN_EMBED_MIN_COS", "0.45"))


def _api_key() -> str:
    return os.environ.get("DASHSCOPE_API_KEY", "").strip()


def available() -> bool:
    """True when semantic search is usable (an embedding key is configured)."""
    return bool(_api_key())


def _embed(texts: list[str]) -> list[list[float]] | None:
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
    except Exception as exc:  # noqa: BLE001
        log.warning("embedding request failed: %s", exc)
        return None


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(_INDEX_DB), timeout=5.0)
    con.execute(
        "CREATE TABLE IF NOT EXISTS chunks "
        "(id TEXT PRIMARY KEY, source TEXT, ref TEXT, text TEXT, "
        "vec BLOB, updated_at TEXT)"
    )
    return con


def _chunk_id(text: str) -> str:
    # Same scheme as the voice module ("memory:<text>") → shared chunks.
    return hashlib.sha1(f"memory:{text}".encode()).hexdigest()[:16]


def _gather() -> dict[str, tuple[str, str]]:
    """{id: (ref, text)} for every memory-file line worth indexing."""
    out: dict[str, tuple[str, str]] = {}
    if not _MEM_DIR.is_dir():
        return out
    for f in sorted(_MEM_DIR.glob("**/*.md")):
        try:
            lines = f.read_text().splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            t = line.strip("-* \t")[:400]
            if len(t) >= _MIN_CHARS:
                out[_chunk_id(t)] = (f"{f.name}:{i}", t)
    return out


def reindex() -> int:
    """Embed memory lines new since last run; returns count embedded."""
    if not available():
        return 0
    now = _gather()
    con = _connect()
    try:
        have = {
            r[0] for r in con.execute("SELECT id FROM chunks WHERE source='memory'")
        }
        todo = [(cid, v) for cid, v in now.items() if cid not in have]
        added = 0
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for i in range(0, len(todo[:_MAX_NEW]), _BATCH):
            batch = todo[i : i + _BATCH]
            vecs = _embed([t for _, (_, t) in batch])
            if vecs is None:
                break
            con.executemany(
                "INSERT OR REPLACE INTO chunks VALUES (?, 'memory', ?, ?, ?, ?)",
                [
                    (cid, ref, t, struct.pack(f"{len(v)}f", *v), ts)
                    for (cid, (ref, t)), v in zip(batch, vecs)
                ],
            )
            added += len(batch)
        con.commit()
        return added
    finally:
        con.close()


def _cosine(a: list[float], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _terms(text: str) -> set[str]:
    return {w for w in text.lower().replace(",", " ").split() if len(w) > 2}


def search(query: str, limit: int = 8) -> list[dict]:
    """Top memory chunks by meaning. [] when unavailable (caller falls back)."""
    q = (query or "").strip()
    if not q or not available():
        return []
    reindex()
    qv = _embed([q])
    if not qv:
        return []
    qvec, qterms = qv[0], _terms(q)
    con = _connect()
    try:
        rows = con.execute("SELECT ref, text, vec FROM chunks").fetchall()
    finally:
        con.close()
    scored: list[tuple[float, dict]] = []
    for ref, text, blob in rows:
        cos = _cosine(qvec, struct.unpack(f"{len(blob) // 4}f", blob))
        if cos < _MIN_COS:
            continue
        kw = len(qterms & _terms(text)) / len(qterms) if qterms else 0.0
        scored.append((_VEC_WEIGHT * cos + _KW_WEIGHT * kw, {"ref": ref, "text": text}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:limit]]
