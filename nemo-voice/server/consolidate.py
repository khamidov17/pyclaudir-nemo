"""Memory consolidation — Nemo's nightly "dreaming" pass.

Once per local day (after CONSOLIDATE_HOUR), one cheap text-model call reads
the last day of episodes and distills what raw fact-extraction can't see:

* insights  — patterns across the day ("stressed about the fine-tune all week")
* people    — who came up and what they mean to Avazbek
* mood      — one line on how the day felt

Everything lands in memory v2 as facts (source ``consolidation``, subject
``insight`` / ``person:<name>`` / ``mood:<date>``) so recall and the session
profile pick them up like any other memory. Failures skip a day, never crash.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

import memory_search
import memory_store

LOG = logging.getLogger("nemo.consolidate")

_MODEL = os.environ.get("QWEN_TEXT_MODEL", "qwen-flash")
_URL = os.environ.get(
    "QWEN_TEXT_URL",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
)
_HOUR = int(os.environ.get("CONSOLIDATE_HOUR", "4"))
_UTC_OFFSET_HOURS = int(os.environ.get("NEMO_UTC_OFFSET", "5"))
_MIN_EPISODES = int(os.environ.get("CONSOLIDATE_MIN_EPISODES", "10"))
_MAX_EPISODES = 300

_PROMPT = (
    "You are the nightly memory-consolidation pass of Avazbek's assistant. "
    "Below is what he said over the last day. Distill it into JSON:\n"
    '- "insights": patterns or arcs worth remembering long-term (not single '
    "facts — those are extracted elsewhere). 0-4 short sentences.\n"
    '- "people": [{"name": ..., "note": one sentence on who they are / what '
    "changed}] for anyone who mattered.\n"
    '- "mood": one short sentence on how his day felt, or null.\n'
    "Reply with ONLY the JSON object. If nothing is worth keeping: "
    '{"insights": [], "people": [], "mood": null}.'
)


def _pointer_path() -> Path:
    base = Path(
        os.environ.get("NEMO_VOICE_DATA_DIR")
        or (Path(__file__).resolve().parents[2] / "data")
    )
    return base / "memory_v2_consolidate.json"


def _local_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=_UTC_OFFSET_HOURS)


def _last_run_day() -> str:
    try:
        return str(json.loads(_pointer_path().read_text()).get("day", ""))
    except (OSError, json.JSONDecodeError):
        return ""


def _mark_run(day: str) -> None:
    try:
        path = _pointer_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"day": day}))
    except OSError as exc:
        LOG.warning("consolidate pointer write failed: %s", exc)


def due() -> bool:
    """Once per local day, only after the quiet consolidation hour."""
    now = _local_now()
    return now.hour >= _HOUR and _last_run_day() != now.strftime("%Y-%m-%d")


def _day_episodes() -> str:
    rows = memory_store.episodes_since(0, limit=100000)[-_MAX_EPISODES:]
    user_lines = [t for _, role, t in rows if role == "user"]
    return "\n".join(user_lines)


async def _call_model(key: str, convo: str) -> dict:
    body = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": convo},
        ],
        "max_tokens": 500,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(
            _URL, headers={"Authorization": f"Bearer {key}"}, json=body
        ) as r:
            data = await r.json()
    parsed = json.loads(data["choices"][0]["message"]["content"])
    return parsed if isinstance(parsed, dict) else {}


def _apply_insights(parsed: dict) -> int:
    written = 0
    for insight in parsed.get("insights") or []:
        if isinstance(insight, str) and insight.strip():
            memory_store.add_fact(
                insight.strip(), subject="insight", source="consolidation"
            )
            written += 1
    return written


def _apply_people(parsed: dict) -> int:
    written = 0
    for person in parsed.get("people") or []:
        if isinstance(person, dict) and (person.get("note") or "").strip():
            name = str(person.get("name") or "someone").strip()[:40]
            memory_store.add_fact(
                person["note"].strip(),
                subject=f"person:{name}",
                source="consolidation",
            )
            written += 1
    return written


def _apply_mood(parsed: dict, day: str) -> int:
    mood = parsed.get("mood")
    if not (isinstance(mood, str) and mood.strip()):
        return 0
    memory_store.add_fact(
        mood.strip(), subject=f"mood:{day}", confidence=0.6, source="consolidation"
    )
    return 1


def _apply(parsed: dict, day: str) -> int:
    return _apply_insights(parsed) + _apply_people(parsed) + _apply_mood(parsed, day)


async def maybe_run() -> int:
    """Run the daily pass if due. Returns facts written (0 = skipped/failed)."""
    if not due():
        return 0
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    day = _local_now().strftime("%Y-%m-%d")
    if not key:
        return 0
    convo = _day_episodes()
    if convo.count("\n") + 1 < _MIN_EPISODES:
        _mark_run(day)  # quiet day — don't retry all day long
        return 0
    try:
        parsed = await _call_model(key, convo)
    except Exception as exc:  # noqa: BLE001 — try again tomorrow
        LOG.warning("consolidation failed (skipping today): %s", exc)
        _mark_run(day)
        return 0
    written = _apply(parsed, day)
    _mark_run(day)
    memory_search.embed_pending()
    LOG.info("consolidation: %d memory item(s) written for %s", written, day)
    return written
