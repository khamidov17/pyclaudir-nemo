"""_QwenPump and helpers — extracted from qwen_realtime to keep it under 300 lines.

Contains history seeding, the client relay, session runner, and re-exports
of all public symbols so callers only need to `import qwen_pump`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

import base64

import fact_extractor
import memory_migrate
import memory_store
import narration
import navigation
import speaker_gate
import voice_facts
import voice_history
from qwen_link import QwenLink

# Re-export everything tests and callers reference directly.
from pump_tools import (  # noqa: F401
    _BG_TOOLS,
    _SENSITIVE_TOOLS,
    _SessionCtx,
    _deliver_via_engine,
    _handle_tool,
    _run_bg_tool,
    _tool_output,
    _track_usage,
)
from pump_class import _QwenPump  # noqa: F401

LOG = logging.getLogger("nemo.qwen_pump")


# ── history seeding ───────────────────────────────────────────────────────────


async def _seed_history(qwen) -> None:
    """Rebuild recent conversation as real items so a reconnected session has momentum."""
    try:
        items = voice_history.recent_items()
    except Exception as exc:  # noqa: BLE001
        LOG.debug("history seed skipped: %s", exc)
        return
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            continue
        is_user = it.get("role") == "user"
        content_type = "input_text" if is_user else "text"
        try:
            await qwen.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user" if is_user else "assistant",
                            "content": [{"type": content_type, "text": text}],
                        },
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001
            LOG.debug("history seed stopped: %s", exc)
            return
    if items:
        LOG.info("seeded %d recent turns into the session", len(items))


# ── client relay ──────────────────────────────────────────────────────────────


# Speaker-gate tee: cap the per-turn buffer at ~30s of 16k mono PCM so a
# never-ending stream can't grow memory unbounded.
_MAX_TEE_BYTES = 16000 * 2 * 30


def _tee_speaker_audio(ctx, b64: str) -> None:
    if ctx is None or not speaker_gate.enabled():
        return
    buf = ctx.speaker.pcm_buf
    if len(buf) >= _MAX_TEE_BYTES:
        return
    try:
        buf.extend(base64.b64decode(b64))
    except (ValueError, TypeError):
        pass


async def _on_location_frame(link: QwenLink, data: dict) -> None:
    """GPS fix from the phone → navigation announcements woven into speech.
    Coordinates are used in-memory only — never journaled."""
    try:
        lat, lon = float(data.get("lat", 0)), float(data.get("lon", 0))
    except (TypeError, ValueError):
        return
    if not navigation.active() or not (lat or lon):  # (0,0) = no real GPS fix
        return
    for text in await navigation.on_location(lat, lon):
        prompt = (
            f"[Navigation guidance — say this naturally in the conversation's "
            f"language, nothing else: {text}]"
        )
        # Background: waiting for a conversation gap must not stall the mic
        # relay this loop also carries.
        link.spawn_bg(lambda _p=prompt: link.inject_text_when_idle(_p))


def _on_narration_frame(link: QwenLink, ctx, data: dict) -> None:
    """Camera frame during scene-narration mode → a short spoken description.
    Runs the VL call + inject in the background so the mic relay never stalls;
    frames are used in-memory only, never journaled."""
    if ctx is None or not getattr(ctx, "narrating", False):
        return
    image_b64 = data.get("image_b64") or data.get("imageB64")
    if not image_b64:
        return

    async def _describe() -> None:
        desc = await asyncio.to_thread(narration.describe_frame, image_b64)
        if desc:
            await link.inject_text_when_idle(
                f"[Scene narration — say this to Avazbek, nothing else: {desc}]"
            )

    link.spawn_bg(_describe)


async def _recv_client(client_ws, link: QwenLink, bridge, ctx=None) -> None:
    """App → Qwen: stream mic audio, resolve tool results, inject text."""
    async for raw in client_ws:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg_type = data.get("type")
        if msg_type == "action_result":
            bridge.resolve(data)
        elif msg_type == "audio":
            b64 = data.get("data", "")
            if b64:
                _tee_speaker_audio(ctx, b64)
                await link.send({"type": "input_audio_buffer.append", "audio": b64})
        elif msg_type == "location":
            await _on_location_frame(link, data)
        elif msg_type == "narration_frame":
            _on_narration_frame(link, ctx, data)
        elif msg_type == "inject":
            text = data.get("text", "")
            if text:
                await link.inject_text(text)


# ── session runner ────────────────────────────────────────────────────────────


@dataclass
class _QwenConnCfg:
    """Qwen connection parameters bundled to reduce argument counts."""

    api_key: str
    url: str
    model: str
    session_config: dict
    voice: str | None = None


async def _run_tasks(to_qwen, to_client) -> None:
    """Wait for the first of the two relay tasks, cancel the other."""
    done, pending = await asyncio.wait(
        {to_qwen, to_client}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


async def _run_session_inner(
    client_ws, qwen, cfg: _QwenConnCfg, orchestrator_factory
) -> None:
    """Inner body of run_qwen_session — called inside the websocket context."""
    from action_bridge import ActionBridge

    if memory_store.memory_v2_enabled() and not memory_migrate.migrated():
        await asyncio.to_thread(memory_migrate.migrate_legacy)
    await qwen.recv()  # session.created
    await qwen.send(json.dumps(cfg.session_config))
    LOG.info("Qwen realtime session open (model=%s, voice=%s)", cfg.model, cfg.voice)
    await _seed_history(qwen)
    await client_ws.send(json.dumps({"type": "ready"}))
    bridge = ActionBridge(client_ws)
    link = QwenLink(qwen)
    orch = orchestrator_factory(link) if orchestrator_factory else None
    ctx = _SessionCtx(client_ws=client_ws, bridge=bridge)
    to_qwen = asyncio.create_task(_recv_client(client_ws, link, bridge, ctx))
    to_client = asyncio.create_task(_QwenPump(link, ctx, orch).run())
    try:
        await _run_tasks(to_qwen, to_client)
    finally:
        await link.aclose()
        if orch is not None:
            orch.close()
        try:
            if memory_store.memory_v2_enabled():
                await fact_extractor.maybe_extract()
            else:
                await voice_facts.maybe_extract()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("fact extraction failed: %s", exc)


async def run_qwen_session(
    client_ws,
    *,
    cfg: _QwenConnCfg,
    orchestrator_factory=None,
) -> None:
    """Run one Qwen realtime session. Called from qwen_realtime.run_session."""
    import websockets

    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    async with websockets.connect(
        f"{cfg.url}?model={cfg.model}", additional_headers=headers
    ) as qwen:
        await _run_session_inner(client_ws, qwen, cfg, orchestrator_factory)
