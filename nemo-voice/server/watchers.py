"""Proactive event sources — things worth telling Avazbek about unprompted.

Each watcher is a poll function returning Events; the proactive loop decides
delivery (interrupt_policy) and calls the watcher's ack so nothing fires twice.

Live sources:
* due follow-ups — commitments the fact extractor filed with a due time
* infra errors  — new ERROR entries in Nemo's own error journal

Inbox/calendar watchers plug in here once their data sources land (message
awareness is engine-side today; there is no calendar feed yet).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import memory_store

LOG = logging.getLogger("nemo.watchers")

_UTC_OFFSET_HOURS = int(os.environ.get("NEMO_UTC_OFFSET", "5"))
_ERROR_LINE = re.compile(r"\*\*\[ERROR\]\*\* `([^`]+)` — (.+)")


@dataclass(frozen=True)
class Event:
    source: str  # 'followup' | 'infra'
    severity: str  # low | normal | high | critical
    text: str  # what Nemo should say / push
    key: str  # dedup identity across polls
    ref_id: int = 0  # source row id, for ack


def _now_local() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=_UTC_OFFSET_HOURS)


# ── due follow-ups ──────────────────────────────────────────────────────────


def poll_followups() -> list[Event]:
    """Open follow-ups whose due time has passed (due_ts is Avazbek-local)."""
    now = _now_local().strftime("%Y-%m-%d %H:%M")
    out = []
    for fid, text, due in memory_store.open_followups():
        if due and due[:16] <= now:
            out.append(
                Event("followup", "normal", f"Follow-up: {text}", f"fu:{fid}", fid)
            )
    return out


def ack_followup(event: Event) -> None:
    memory_store.resolve_followup(event.ref_id, status="notified")


# ── infra errors (Nemo's own error journal) ────────────────────────────────


def _journal_path() -> Path:
    base = Path(
        os.environ.get("NEMO_VOICE_DATA_DIR")
        or (Path(__file__).resolve().parents[2] / "data")
    )
    return base / "nemo_error_log.md"


def _offset_path() -> Path:
    return _journal_path().with_name("infra_watch_offset.json")


def _read_offset() -> int:
    try:
        return int(json.loads(_offset_path().read_text()).get("line", 0))
    except (OSError, json.JSONDecodeError, ValueError):
        return 0


def poll_infra() -> list[Event]:
    """New ERROR entries in the error journal since the last acked poll."""
    path = _journal_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    start = _read_offset()
    out = []
    for i, line in enumerate(lines[start:], start=start):
        m = _ERROR_LINE.search(line)
        if m:
            source, message = m.group(1), m.group(2)
            out.append(
                Event(
                    "infra",
                    "high",
                    f"{source} hit an error: {message[:150]}",
                    f"infra:{i}",
                    i,
                )
            )
    return out


def ack_infra(event: Event) -> None:
    """Advance the offset past this entry so it never re-fires."""
    try:
        path = _offset_path()
        current = _read_offset()
        path.write_text(json.dumps({"line": max(current, event.ref_id + 1)}))
    except OSError as exc:
        LOG.warning("infra offset write failed: %s", exc)


# ── registry ────────────────────────────────────────────────────────────────


def _study_watcher() -> tuple:
    import study_coach

    return ("study", study_coach.poll_study, study_coach.ack_study)


def _health_watcher() -> tuple:
    import health

    return ("health", health.poll_health, health.ack_health)


def _build_watchers() -> tuple:
    """Core watchers always; optional ones guarded so a broken optional module
    can't fail this import and take the whole proactive loop down with it."""
    watchers: list[tuple] = [
        ("followup", poll_followups, ack_followup),
        ("infra", poll_infra, ack_infra),
    ]
    for factory in (_study_watcher, _health_watcher):
        try:
            watchers.append(factory())
        except Exception as exc:  # noqa: BLE001
            LOG.warning("optional watcher unavailable: %s", exc)
    return tuple(watchers)


WATCHERS = _build_watchers()


def poll_all() -> list[Event]:
    out: list[Event] = []
    for name, poll, _ack in WATCHERS:
        try:
            out.extend(poll())
        except Exception as exc:  # noqa: BLE001 — one broken watcher ≠ dead loop
            LOG.warning("watcher %s failed: %s", name, exc)
    return out


def ack(event: Event) -> None:
    for name, _poll, do_ack in WATCHERS:
        if event.source == name:
            try:
                do_ack(event)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("ack %s failed: %s", event.key, exc)
            return
