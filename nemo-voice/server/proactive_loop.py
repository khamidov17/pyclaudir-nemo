"""The proactive heartbeat — poll watchers, route events, deliver.

Every tick: gather events from watchers.poll_all(), decide each with
interrupt_policy, then

* SPEAK — weave into the live voice session (same path as /internal/proactive)
* PUSH  — reminders.notify_now → the engine's phone-notification rail
* DEFER — leave un-acked; the watcher re-emits it and it's re-decided next
          tick, so deferred events deliver themselves when quiet hours end
          or severity context changes.

Started from voice_http.serve behind VOICE_PROACTIVE_V2=1. Watcher and
delivery failures are logged and skipped — this loop must never die.
"""

from __future__ import annotations

import asyncio
import logging
import os

import consolidate
import interrupt_policy
import interruption_log
import reminders
import session_registry
import watchers
from interrupt_policy import Decision

LOG = logging.getLogger("nemo.proactive_loop")

_POLL_SEC = float(os.environ.get("VOICE_PROACTIVE_POLL_SEC", "60"))


def enabled() -> bool:
    return os.environ.get("VOICE_PROACTIVE_V2", "0").strip() == "1"


async def _speak(text: str) -> bool:
    orch = session_registry.any_active()
    if orch is None:
        return False
    chunk = f"[proactive — mention this naturally, do not read verbatim:] {text}"
    # rev=-1 → proactive: never rev-stale, unlike topic-tied engine chunks.
    # Return the ACTUAL delivery result: if weave-in dropped it (sensitive turn,
    # closed session), _deliver falls back to a phone push instead of acking a
    # lost event.
    return bool(
        await orch.on_background_chunk(chunk, True, -1)  # type: ignore[attr-defined]
    )


async def _deliver(event: watchers.Event, decision: Decision) -> bool:
    """True if the event left the building (and should be acked)."""
    if decision is Decision.DEFER:
        return False
    if decision is Decision.SPEAK and await _speak(event.text):
        return True
    # PUSH, or SPEAK whose session vanished between decide and deliver.
    await asyncio.to_thread(reminders.notify_now, event.text)
    return True


async def tick() -> int:
    """One poll-decide-deliver pass; returns how many events were delivered."""
    events = await asyncio.to_thread(watchers.poll_all)
    delivered = 0
    for event in events:
        decision = interrupt_policy.decide(
            event.severity, session_registry.any_active() is not None
        )
        # Learned demotion: sources he keeps ignoring stop interrupting him.
        if decision is Decision.SPEAK and interruption_log.demoted(event.source):
            decision = Decision.PUSH
        try:
            if await _deliver(event, decision):
                await asyncio.to_thread(watchers.ack, event)
                await asyncio.to_thread(
                    interruption_log.record_delivery, event.source, decision.value
                )
                delivered += 1
                LOG.info("proactive %s → %s: %s", event.key, decision, event.text[:80])
        except Exception as exc:  # noqa: BLE001 — skip this event, keep the loop
            LOG.warning("delivery failed for %s: %s", event.key, exc)
    return delivered


async def run() -> None:
    """The forever loop. Cancelled with the server."""
    LOG.info("proactive loop running (every %.0fs)", _POLL_SEC)
    while True:
        try:
            await tick()
            await consolidate.maybe_run()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("proactive tick failed: %s", exc)
        await asyncio.sleep(_POLL_SEC)
