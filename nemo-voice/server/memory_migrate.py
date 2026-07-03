"""One-shot import of legacy memory into the v2 store.

Sources:
* ``data/memories/**/*.md`` bullet lines → facts (source ``legacy:<file>``)
* ``data/voice_journal.jsonl`` user turns  → episodes (session ``legacy``)

Idempotent via a marker file — safe to call on every startup; only the first
call with VOICE_MEMORY_V2 does work. Legacy files are left untouched: the old
path keeps working until v2 is proven and the flag flips on for good.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import memory_store

LOG = logging.getLogger("nemo.memory_migrate")

_JOURNAL_TAIL = 2000
_MIN_CHARS = 8


def _data_dir() -> Path:
    return Path(
        os.environ.get("NEMO_VOICE_DATA_DIR")
        or (Path(__file__).resolve().parents[2] / "data")
    )


def _marker() -> Path:
    return _data_dir() / "memory_v2_migrated.json"


def migrated() -> bool:
    return _marker().exists()


def _import_memory_files(mem_dir: Path) -> int:
    count = 0
    for f in sorted(mem_dir.glob("**/*.md")):
        try:
            lines = f.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            text = line.strip("-•* \t")
            if len(text) >= _MIN_CHARS and not text.startswith("#"):
                memory_store.add_fact(text[:500], source=f"legacy:{f.name}")
                count += 1
    return count


def _import_journal(journal: Path) -> int:
    try:
        lines = journal.read_text().splitlines()[-_JOURNAL_TAIL:]
    except OSError:
        return 0
    count = 0
    for raw in lines:
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if item.get("role") != "user":
            continue
        text = (item.get("text") or "").strip()
        if len(text) >= _MIN_CHARS:
            memory_store.add_episode("legacy", "user", text)
            count += 1
    return count


def migrate_legacy() -> dict[str, int]:
    """Import legacy memory once. Returns counts; {} when already done."""
    if migrated():
        return {}
    base = _data_dir()
    facts = (
        _import_memory_files(base / "memories") if (base / "memories").is_dir() else 0
    )
    journal = base / "voice_journal.jsonl"
    episodes = _import_journal(journal) if journal.exists() else 0
    try:
        _marker().parent.mkdir(parents=True, exist_ok=True)
        _marker().write_text(json.dumps({"facts": facts, "episodes": episodes}))
    except OSError as exc:
        LOG.warning("marker write failed: %s", exc)
    LOG.info("memory_v2 migration: %d facts, %d episodes", facts, episodes)
    return {"facts": facts, "episodes": episodes}
