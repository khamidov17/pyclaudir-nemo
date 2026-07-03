"""Voice conversation memory: a rolling window for prompt injection + a
permanent, searchable journal of everything said.

Two stores:
- voice_recent.json  — last N turns, re-injected into each new session so a
  reconnect continues seamlessly.
- voice_journal.jsonl — every turn ever, append-only, searched by `recall` so
  Nemo can remember anything from any past voice conversation (not just a
  summary). Cheap: plain file appends, no API cost.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

import memory_store

LOG = logging.getLogger("nemo.voice_history")

_DATA_DIR = Path(
    os.environ.get("NEMO_VOICE_DATA_DIR")
    or (Path(__file__).resolve().parents[2] / "data")
)
_RECENT = _DATA_DIR / "voice_recent.json"
_JOURNAL = _DATA_DIR / "voice_journal.jsonl"
_MAX_TURNS = 24
# Cap the permanent journal so months of daily use can't grow it without bound.
# When it passes the size cap, keep the most recent lines (recall searches the
# tail anyway).
_MAX_JOURNAL_BYTES = 5 * 1024 * 1024
_KEEP_LINES = 5000


def _load_recent() -> list[dict]:
    try:
        return json.loads(_RECENT.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def add(role: str, text: str) -> None:
    """Record one spoken turn (role: 'user' or 'nemo') in both stores."""
    text = (text or "").strip()
    if not text:
        return
    items = _load_recent()
    items.append({"role": role, "text": text[:300], "ts": time.time()})
    items = items[-_MAX_TURNS:]
    if memory_store.memory_v2_enabled():
        try:
            memory_store.add_episode("voice", role, text)
        except Exception as exc:  # noqa: BLE001 — v2 mirror must not break the turn
            LOG.warning("episode mirror failed: %s", exc)
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _RECENT.write_text(json.dumps(items))
        with _JOURNAL.open("a") as f:
            f.write(json.dumps({"role": role, "text": text[:2000]}) + "\n")
        _rotate_journal()
    except OSError as exc:
        LOG.warning("voice history write failed: %s", exc)


def _rotate_journal() -> None:
    """Trim the journal to its most recent lines once it passes the size cap.

    Writes to a temp file then atomically os.replace()s it in, so readers never
    see a half-written/partial journal. (Called inline from add() in a single
    writer; it does NOT guard against a second concurrent writer.)
    """
    try:
        if not _JOURNAL.exists() or _JOURNAL.stat().st_size < _MAX_JOURNAL_BYTES:
            return
        lines = _JOURNAL.read_text().splitlines()[-_KEEP_LINES:]
        fd, tmp = tempfile.mkstemp(dir=str(_DATA_DIR), suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, _JOURNAL)
        LOG.info("voice journal rotated → kept last %d lines", len(lines))
    except OSError as exc:
        LOG.warning("journal rotate failed: %s", exc)


def recent(limit: int = 18) -> str:
    """The last `limit` spoken turns, formatted for the prompt (oldest first)."""
    items = _load_recent()[-limit:]
    return "\n".join(
        f"{'Avazbek' if it.get('role') == 'user' else 'Nemo'}: {it.get('text', '')}"
        for it in items
    ).strip()


def recent_items(max_age_sec: float = 900.0, limit: int = 18) -> list[dict]:
    """Recent turns as structured {role, text} — for SEEDING a reconnected
    session as real conversation history (genuine momentum, not a described
    transcript). Returns [] if the last turn is older than max_age_sec: that's a
    fresh conversation, not a reconnect, so Nemo shouldn't 'continue' a stale one.
    """
    items = _load_recent()
    if not items:
        return []
    now = time.time()
    if now - items[-1].get("ts", 0) > max_age_sec:
        return []  # stale → treat as a brand-new conversation
    fresh = [it for it in items if now - it.get("ts", 0) <= max_age_sec]
    return [{"role": it["role"], "text": it.get("text", "")} for it in fresh[-limit:]]


def search(query: str, limit: int = 8) -> list[str]:
    """Find matching past spoken turns from the permanent journal."""
    q = (query or "").lower().strip()
    if not q or not _JOURNAL.exists():
        return []
    try:
        lines = _JOURNAL.read_text().splitlines()[-3000:]
    except OSError:
        return []
    out: list[str] = []
    for ln in reversed(lines):
        try:
            it = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if q in it.get("text", "").lower():
            who = "Avazbek" if it.get("role") == "user" else "Nemo"
            out.append(f"{who}: {it.get('text', '')[:200]}")
            if len(out) >= limit:
                break
    return list(reversed(out))
