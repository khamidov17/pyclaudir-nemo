"""Automatically write durable facts about Avazbek from voice conversations.

Like the text/Telegram Nemo, the voice Nemo should save facts on its own rather
than relying on the realtime model to call a tool. After enough new turns, this
runs ONE cheap text-model pass over the new journal turns, extracts personal
facts, and appends the novel ones to data/memories/voice_facts.md — which
voice_brain already injects into every session's prompt and `recall` searches.

Cost control: gated on a minimum number of new turns (so frequent reconnects
don't re-run it), one short call to a cheap model, deduped against known facts.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import aiohttp

LOG = logging.getLogger("nemo.voice_facts")

_DATA_DIR = Path(
    os.environ.get("NEMO_VOICE_DATA_DIR")
    or (Path(__file__).resolve().parents[2] / "data")
)
_JOURNAL = _DATA_DIR / "voice_journal.jsonl"
_FACTS = _DATA_DIR / "memories" / "voice_facts.md"
_POINTER = _DATA_DIR / "voice_facts_pointer.json"

_MODEL = os.environ.get("QWEN_TEXT_MODEL", "qwen-flash")
_URL = os.environ.get(
    "QWEN_TEXT_URL",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
)
_MIN_NEW_TURNS = int(os.environ.get("VOICE_FACTS_MIN_NEW", "6"))

_EXTRACT_PROMPT = (
    "From this voice conversation, extract durable personal facts about Avazbek "
    "— preferences, plans, names, relationships, habits, his work, his life. "
    "One short fact per line, no numbering. Skip small talk and anything "
    "transient. If there are no durable facts, reply with exactly: NONE"
)


def _pointer() -> int:
    try:
        return int(json.loads(_POINTER.read_text()).get("done", 0))
    except (OSError, json.JSONDecodeError, ValueError):
        return 0


def _save_pointer(n: int) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _POINTER.write_text(json.dumps({"done": n}))
    except OSError as exc:
        LOG.warning("pointer write failed: %s", exc)


def _format(lines: list[str]) -> str:
    out = []
    for ln in lines:
        try:
            it = json.loads(ln)
        except json.JSONDecodeError:
            continue
        who = "Avazbek" if it.get("role") == "user" else "Nemo"
        out.append(f"{who}: {it.get('text', '')}")
    return "\n".join(out)


def _append_new(facts: list[str]) -> int:
    existing = _FACTS.read_text().lower() if _FACTS.exists() else ""
    fresh = [f for f in facts if f and f.lower()[:60] not in existing]
    if not fresh:
        return 0
    _FACTS.parent.mkdir(parents=True, exist_ok=True)
    with _FACTS.open("a") as f:
        for fact in fresh:
            f.write(f"- {fact}\n")
    return len(fresh)


async def _call_model(key: str, convo: str) -> list[str]:
    body = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": _EXTRACT_PROMPT},
            {"role": "user", "content": convo},
        ],
        "max_tokens": 300,
        "temperature": 0,
    }
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(
            _URL, headers={"Authorization": f"Bearer {key}"}, json=body
        ) as r:
            data = await r.json()
    text = data["choices"][0]["message"]["content"]
    return [
        ln.lstrip("-•* ").strip()
        for ln in text.splitlines()
        if ln.strip() and "NONE" not in ln.strip().upper()
    ]


async def maybe_extract() -> None:
    """Extract + save facts if enough new turns have accumulated. Safe to call
    on every session end; cheap and self-rate-limiting."""
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key or not _JOURNAL.exists():
        return
    try:
        lines = _JOURNAL.read_text().splitlines()
    except OSError:
        return
    done = _pointer()
    new = lines[done:]
    if len(new) < _MIN_NEW_TURNS:
        return
    facts = await _call_model(key, _format(new))
    saved = _append_new(facts)
    _save_pointer(len(lines))
    LOG.info("voice_facts: %d new fact(s) saved from %d turns", saved, len(new))
