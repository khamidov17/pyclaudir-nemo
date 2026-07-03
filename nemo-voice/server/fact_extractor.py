"""Memory v2 extraction — episodes → structured facts, procedures, follow-ups.

The v2 replacement for voice_facts.py: same trigger (session end, gated on a
minimum number of new turns, one cheap qwen-flash call) but structured output
into memory_store instead of a flat markdown file, with dedup/supersede against
existing facts instead of substring matching.

The model returns JSON: {"facts": [...], "procedures": [...], "followups":
[{"text": ..., "due": "YYYY-MM-DD HH:MM"|null}]}. For each candidate fact we
check nearest live facts: near-duplicates are skipped; a close-but-different
fact on the same topic supersedes the old one (old row kept, marked).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

import memory_search
import memory_store

LOG = logging.getLogger("nemo.fact_extractor")

_MODEL = os.environ.get("QWEN_TEXT_MODEL", "qwen-flash")
_URL = os.environ.get(
    "QWEN_TEXT_URL",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
)
_MIN_NEW_TURNS = int(os.environ.get("VOICE_FACTS_MIN_NEW", "6"))
# Cosine bands for the upsert decision, tuned like memory_index's floor:
# ≥ DUP  → same fact, skip; ≥ RELATED → same topic, new fact supersedes old.
_DUP_COS = float(os.environ.get("VOICE_FACT_DUP_COS", "0.90"))
_RELATED_COS = float(os.environ.get("VOICE_FACT_RELATED_COS", "0.75"))

_PROMPT = (
    "You maintain long-term memory for a voice assistant. From this "
    "conversation with Avazbek, extract:\n"
    '- "facts": durable personal facts (preferences, people, plans, work, '
    "habits). One concise sentence each.\n"
    '- "procedures": standing instructions on HOW he wants things done '
    '("when I say X, do Y").\n'
    '- "followups": commitments or things to check back on, each '
    '{"text": ..., "due": "YYYY-MM-DD HH:MM" or null}.\n'
    "Skip small talk and anything transient. Reply with ONLY a JSON object "
    'like {"facts": [], "procedures": [], "followups": []}.'
)


@dataclass
class ExtractReport:
    facts_added: int = 0
    facts_superseded: int = 0
    procedures_added: int = 0
    followups_added: int = 0
    skipped_dups: int = 0
    errors: list[str] = field(default_factory=list)


def _pointer_path() -> Path:
    base = Path(
        os.environ.get("NEMO_VOICE_DATA_DIR")
        or (Path(__file__).resolve().parents[2] / "data")
    )
    return base / "memory_v2_pointer.json"


def _pointer() -> int:
    try:
        return int(json.loads(_pointer_path().read_text()).get("episode_id", 0))
    except (OSError, json.JSONDecodeError, ValueError):
        return 0


def _save_pointer(episode_id: int) -> None:
    try:
        path = _pointer_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"episode_id": episode_id}))
    except OSError as exc:
        LOG.warning("pointer write failed: %s", exc)


async def _call_model(key: str, convo: str) -> dict:
    body = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": convo},
        ],
        "max_tokens": 600,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(
            _URL, headers={"Authorization": f"Bearer {key}"}, json=body
        ) as r:
            data = await r.json()
    parsed = json.loads(data["choices"][0]["message"]["content"])
    return parsed if isinstance(parsed, dict) else {}


def _upsert_fact(text: str, report: ExtractReport) -> None:
    """Add a fact unless it duplicates a live one; supersede near-matches."""
    similar = memory_search.find_similar_facts(text, k=1)
    if similar and similar[0][0] >= _DUP_COS:
        report.skipped_dups += 1
        return
    if not similar and _exact_live_dup(text):
        # Embeddings unavailable → find_similar_facts returns []; fall back to an
        # exact-text check so an embed outage doesn't re-insert the same fact
        # every extraction pass.
        report.skipped_dups += 1
        return
    new_id = memory_store.add_fact(text, source="voice_extract")
    report.facts_added += 1
    if similar and similar[0][0] >= _RELATED_COS:
        memory_store.supersede_fact(similar[0][1].id, new_id)
        report.facts_superseded += 1


def _exact_live_dup(text: str) -> bool:
    norm = text.strip().lower()
    return any(t.strip().lower() == norm for _, _, t in memory_store.live_facts())


def _clean_strings(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    return [s.strip() for s in items if isinstance(s, str) and s.strip()]


def _apply_followups(parsed: dict, last_episode_id: int, report: ExtractReport) -> None:
    for fu in parsed.get("followups") or []:
        if isinstance(fu, dict) and (fu.get("text") or "").strip():
            memory_store.add_followup(
                fu["text"].strip(), fu.get("due") or None, last_episode_id
            )
            report.followups_added += 1


def _apply(parsed: dict, last_episode_id: int, report: ExtractReport) -> None:
    for fact in _clean_strings(parsed.get("facts")):
        _upsert_fact(fact, report)
    for proc in _clean_strings(parsed.get("procedures")):
        memory_store.add_procedure("", proc)
        report.procedures_added += 1
    _apply_followups(parsed, last_episode_id, report)


async def extract() -> ExtractReport:
    """One extraction pass over episodes newer than the pointer."""
    report = ExtractReport()
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        return report
    new = memory_store.episodes_since(_pointer())
    if len(new) < _MIN_NEW_TURNS:
        return report
    convo = "\n".join(
        f"{'Avazbek' if role == 'user' else 'Nemo'}: {text}" for _, role, text in new
    )
    try:
        parsed = await _call_model(key, convo)
    except Exception as exc:  # noqa: BLE001 — episodes retained, retried next time
        report.errors.append(str(exc))
        LOG.warning("extraction failed (will retry next session): %s", exc)
        return report
    last_id = new[-1][0]
    await asyncio.to_thread(_apply, parsed, last_id, report)
    _save_pointer(last_id)
    await asyncio.to_thread(memory_search.embed_pending)
    LOG.info(
        "memory_v2 extract: +%d facts (%d superseded, %d dup), +%d proc, +%d followups",
        report.facts_added,
        report.facts_superseded,
        report.skipped_dups,
        report.procedures_added,
        report.followups_added,
    )
    return report


async def maybe_extract() -> None:
    """Session-end hook: safe to call every time; gated and self-rate-limiting."""
    try:
        await extract()
    except Exception as exc:  # noqa: BLE001 — never let extraction crash teardown
        LOG.warning("maybe_extract failed: %s", exc)
