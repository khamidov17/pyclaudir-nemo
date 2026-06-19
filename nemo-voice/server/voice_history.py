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
from pathlib import Path

LOG = logging.getLogger("nemo.voice_history")

_DATA_DIR = Path(
    os.environ.get("NEMO_VOICE_DATA_DIR")
    or (Path(__file__).resolve().parents[2] / "data")
)
_RECENT = _DATA_DIR / "voice_recent.json"
_JOURNAL = _DATA_DIR / "voice_journal.jsonl"
_MAX_TURNS = 24


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
    items.append({"role": role, "text": text[:300]})
    items = items[-_MAX_TURNS:]
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _RECENT.write_text(json.dumps(items))
        with _JOURNAL.open("a") as f:
            f.write(json.dumps({"role": role, "text": text[:2000]}) + "\n")
    except OSError as exc:
        LOG.warning("voice history write failed: %s", exc)


def recent(limit: int = 18) -> str:
    """The last `limit` spoken turns, formatted for the prompt (oldest first)."""
    items = _load_recent()[-limit:]
    return "\n".join(
        f"{'Avazbek' if it.get('role') == 'user' else 'Nemo'}: {it.get('text', '')}"
        for it in items
    ).strip()


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
