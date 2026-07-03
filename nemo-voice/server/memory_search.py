"""Embedding + hybrid vector search over the memory v2 store.

Same embedding backend and hybrid-scoring shape as memory_index.py (DashScope
text-embedding-v3, 0.6·cosine + 0.4·keyword, absolute cosine floor), applied to
the structured memory_store tables instead of flat chunks. Degrades to
keyword-only when embeddings are unavailable — recall never hard-fails.

``knn()`` is the sqlite-vec swap point: everything above it only sees ranked
(score, Row) pairs.
"""

from __future__ import annotations

import json
import logging
import math
import os
import urllib.request
from dataclasses import dataclass

import memory_store
from memory_store import pack, unpack

LOG = logging.getLogger("nemo.memory_search")


# ── rows for search / embedding (relocated from memory_store) ──────────────

_TABLES = {"fact": "facts", "episode": "episodes", "procedure": "procedures"}


@dataclass(frozen=True)
class Row:
    """One searchable memory row: kind is 'fact' | 'episode' | 'procedure'."""

    kind: str
    id: int
    text: str
    vec: tuple[float, ...] | None


def searchable_rows(kinds: tuple[str, ...]) -> list[Row]:
    """All live rows of the given kinds with their vectors (None if unembedded)."""
    con = memory_store.connect()
    try:
        out: list[Row] = []
        for kind in kinds:
            table = _TABLES[kind]
            where = "WHERE superseded_by IS NULL" if kind == "fact" else ""
            rows = con.execute(f"SELECT id, text, vec FROM {table} {where}").fetchall()
            out.extend(
                Row(kind, int(i), str(t), unpack(b) if b else None) for i, t, b in rows
            )
        return out
    finally:
        con.close()


def unembedded(kind: str, limit: int = 64) -> list[tuple[int, str]]:
    con = memory_store.connect()
    try:
        rows = con.execute(
            f"SELECT id, text FROM {_TABLES[kind]} WHERE vec IS NULL LIMIT ?",
            (limit,),
        ).fetchall()
        return [(int(i), str(t)) for i, t in rows]
    finally:
        con.close()


def store_vectors(kind: str, vecs: list[tuple[int, list[float]]]) -> None:
    con = memory_store.connect()
    try:
        con.executemany(
            f"UPDATE {_TABLES[kind]} SET vec = ? WHERE id = ?",
            [(pack(v), i) for i, v in vecs],
        )
        con.commit()
    finally:
        con.close()


_EMBED_URL = os.environ.get(
    "QWEN_EMBED_URL",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/embeddings",
)
_EMBED_MODEL = os.environ.get("QWEN_EMBED_MODEL", "text-embedding-v3")
_MIN_COS = float(os.environ.get("QWEN_EMBED_MIN_COS", "0.45"))
_VEC_WEIGHT = 0.6
_KW_WEIGHT = 0.4
# Facts are distilled truths; rank them above raw episode speech.
_KIND_BOOST = {"fact": 0.1, "procedure": 0.1, "episode": 0.0}
_BATCH = 10


def _api_key() -> str:
    # Read at call time: the server loads .env after importing this module.
    return os.environ.get("DASHSCOPE_API_KEY", "").strip()


def available() -> bool:
    return bool(_api_key())


def embed(texts: list[str]) -> list[list[float]] | None:
    """Embed via DashScope; None on any failure (callers degrade to keyword)."""
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


def _cosine(a: list[float], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _terms(text: str) -> set[str]:
    return {w for w in text.lower().replace(",", " ").split() if len(w) > 2}


def _keyword_score(qterms: set[str], text: str) -> float:
    if not qterms:
        return 0.0
    return len(qterms & _terms(text)) / len(qterms)


def knn(qvec: list[float], rows: list[Row]) -> list[tuple[float, Row]]:
    """Cosine over in-memory rows. Swap point for sqlite-vec at scale."""
    out = []
    for row in rows:
        if row.vec is None:
            continue
        cos = _cosine(qvec, row.vec)
        if cos >= _MIN_COS:
            out.append((cos, row))
    return out


def search(
    query: str,
    kinds: tuple[str, ...] = ("fact", "procedure", "episode"),
    limit: int = 6,
) -> list[Row]:
    """Top rows by hybrid score; keyword-only when embeddings are down."""
    q = (query or "").strip()
    if not q:
        return []
    rows = searchable_rows(kinds)
    qterms = _terms(q)
    qv = embed([q]) if available() else None
    scored: list[tuple[float, Row]] = []
    if qv:
        scored = [
            (
                cos * _VEC_WEIGHT
                + _keyword_score(qterms, r.text) * _KW_WEIGHT
                + _KIND_BOOST[r.kind],
                r,
            )
            for cos, r in knn(qv[0], rows)
        ]
    else:
        scored = [
            (kw + _KIND_BOOST[r.kind], r)
            for r in rows
            if (kw := _keyword_score(qterms, r.text)) > 0
        ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]


def find_similar_facts(text: str, k: int = 3) -> list[tuple[float, Row]]:
    """Nearest live facts to `text` — the extractor's dedup/supersede check."""
    qv = embed([text])
    if not qv:
        return []
    hits = knn(qv[0], searchable_rows(("fact",)))
    hits.sort(key=lambda x: x[0], reverse=True)
    return hits[:k]


def embed_pending() -> int:
    """Fill NULL vectors in batches; returns count embedded. Cheap no-op when
    everything is already embedded or no key is configured."""
    if not available():
        return 0
    done = 0
    for kind in ("fact", "episode", "procedure"):
        todo = unembedded(kind)
        for i in range(0, len(todo), _BATCH):
            batch = todo[i : i + _BATCH]
            vecs = embed([t for _, t in batch])
            if vecs is None:
                return done
            store_vectors(kind, [(rid, v) for (rid, _), v in zip(batch, vecs)])
            done += len(batch)
    return done
