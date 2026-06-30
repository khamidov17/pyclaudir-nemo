"""HTTP server for the voice service — /health, static client, /internal/*.

Split from streaming_service.py so each module stays under 300 lines.
The /internal/brain_result endpoint (P3) is registered here; the session
registry and Orchestrator are imported lazily to avoid circular imports.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from aiohttp import web

from error_journal import log_warn
from voice_metrics import METRICS

# Strong references to fire-and-forget tasks so the GC can't collect them mid-flight.
_bg_tasks: set[asyncio.Task] = set()

LOG = logging.getLogger("nemo.voice_http")

_INTERNAL_TOKEN = os.environ.get("VOICE_INTERNAL_TOKEN", "").strip()
if not _INTERNAL_TOKEN:
    LOG.warning(
        "VOICE_INTERNAL_TOKEN is not set — /internal/* endpoints will always return 401."
        " Proactive reminders and brain-result weave-in are disabled."
    )

# UUID4 format guard for session_id from untrusted callers.
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
# XML-tag-like injection patterns stripped from chunk text.
_TAG_RE = re.compile(r"<[^>]{0,80}>")
# Bidi overrides + zero-width/invisible chars used to hide injection markers.
_BIDI_RE = re.compile(
    r"[­؜​-‍‎‏‪-‮⁠⁦-⁩﻿]"
)
# LLM chat-template markers. Applied in a loop so nested forms collapse fully.
_INJECT_RE = re.compile(r"\[/?INST\]|</s>|<s>|\[/?SYS\]", re.IGNORECASE)
# Per-session rate limit: max 60 POSTs/min to /internal/brain_result.
_RATE: dict[str, list[float]] = defaultdict(list)
_RATE_MAX = 60
_RATE_WINDOW = 60.0


def _verify_internal(raw_body: bytes, sig: str) -> bool:
    """HMAC-SHA256 of raw body with VOICE_INTERNAL_TOKEN."""
    if not _INTERNAL_TOKEN:
        return False
    expected = hmac.new(_INTERNAL_TOKEN.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def _sanitize_chunk(text: str) -> str:
    """Strip XML-like tags, bidi overrides, and LLM injection markers; cap length."""
    text.encode("utf-8")  # raises UnicodeEncodeError if invalid
    text = _TAG_RE.sub("", text)
    text = _BIDI_RE.sub("", text)
    while True:
        cleaned = _INJECT_RE.sub("", text)
        if cleaned == text:
            break
        text = cleaned
    return text[:2000]


_RATE_MAX_SESSIONS = 10_000


def _rate_ok(session_id: str) -> bool:
    now = time.monotonic()
    # Evict the least-recently-used entry when the table is full.
    # Insertion-order eviction (next(iter())) would evict the oldest-inserted
    # entry first — which is the legitimate long-lived session, not the attacker's
    # junk. LRU evicts whichever session hasn't been seen most recently instead.
    if len(_RATE) >= _RATE_MAX_SESSIONS and session_id not in _RATE:
        lru_key = min(_RATE, key=lambda k: _RATE[k][-1] if _RATE[k] else 0)
        del _RATE[lru_key]
    bucket = _RATE[session_id]
    _RATE[session_id] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(_RATE[session_id]) >= _RATE_MAX:
        return False
    _RATE[session_id].append(now)
    return True


async def handle_brain_result(request: web.Request) -> web.Response:
    """POST /internal/brain_result — engine streams a clause chunk to this session.

    Auth: X-Internal-Sig = HMAC-SHA256(body, VOICE_INTERNAL_TOKEN).
    Body JSON: {session_id, chunk, final, rev}.
    """
    raw = await request.read()
    sig = request.headers.get("X-Internal-Sig", "")
    if not _verify_internal(raw, sig):
        LOG.warning("brain_result: bad or missing HMAC signature")
        return web.Response(status=401)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return web.Response(status=400)
    session_id = str(payload.get("session_id", ""))
    if not _UUID4_RE.match(session_id):
        return web.Response(status=400)
    if not _rate_ok(session_id):
        LOG.warning("brain_result: rate limit hit for session %s", session_id[:8])
        return web.Response(status=429)
    try:
        chunk = _sanitize_chunk(str(payload.get("chunk", "")))
    except (UnicodeEncodeError, UnicodeDecodeError):
        return web.Response(status=400)
    final = bool(payload.get("final", False))
    rev = int(payload.get("rev", 0))
    import session_registry

    orch = session_registry.get(session_id)
    if orch is None:
        log_warn("voice_http/brain_result", f"session not found for chunk delivery (sid={session_id[:8]}…)")
        return web.Response(status=404)
    t = asyncio.create_task(orch.on_background_chunk(chunk, final, rev))
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return web.Response(status=202)


def _bearer_ok(request: web.Request) -> bool:
    """True if Authorization: Bearer matches VOICE_INTERNAL_TOKEN."""
    if not _INTERNAL_TOKEN:
        return False
    auth = request.headers.get("Authorization", "")
    provided = auth[7:] if auth.startswith("Bearer ") else ""
    return hmac.compare_digest(provided, _INTERNAL_TOKEN)


def _query_ttfsw() -> list[dict]:
    """Return last 50 ttfsw rows as dicts, newest first."""
    from voice_metrics import _DB_PATH, _TTFSW_DDL

    cols = ("id", "session_id", "ts", "ms", "tier", "route")
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(_TTFSW_DDL)
        rows = conn.execute(
            "SELECT id, session_id, ts, ms, tier, route "
            "FROM ttfsw ORDER BY id DESC LIMIT 50"
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


async def handle_debug_latency(request: web.Request) -> web.Response:
    """Return last 50 ttfsw rows as JSON. Requires Bearer VOICE_INTERNAL_TOKEN."""
    if os.environ.get("DEBUG_DASHBOARD", "").strip() != "1":
        return web.Response(status=404)
    if not _bearer_ok(request):
        return web.Response(status=401)
    try:
        rows = _query_ttfsw()
    except sqlite3.Error as exc:
        LOG.warning("debug_latency: query failed: %s", exc)
        rows = []
    return web.json_response({"rows": rows})


async def handle_proactive(request: web.Request) -> web.Response:
    """POST /internal/proactive — engine injects a spoken reminder into the active session.

    Body: {"text": "..."}. Same HMAC auth as /internal/brain_result.
    Delivers to the most recently registered session, or 404 if none.
    """
    raw = await request.read()
    sig = request.headers.get("X-Internal-Sig", "")
    if not _verify_internal(raw, sig):
        return web.Response(status=401)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return web.Response(status=400)
    raw_text = str(payload.get("text", "")).strip()
    if not raw_text:
        return web.Response(status=400)
    try:
        text = _sanitize_chunk(raw_text)
    except (UnicodeEncodeError, UnicodeDecodeError):
        return web.Response(status=400)
    if not text:
        return web.Response(status=400)
    import session_registry

    orch = session_registry.any_active()
    if orch is None:
        log_warn("voice_http/proactive", "proactive reminder arrived but no active voice session — not spoken")
        return web.Response(status=404)
    chunk = f"[proactive reminder — speak this naturally, do not read verbatim:] {text}"
    t = asyncio.create_task(orch.on_background_chunk(chunk, True, 0))  # type: ignore[attr-defined]
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return web.Response(status=202)


async def handle_latency_dashboard(_request: web.Request) -> web.Response:
    """Serve the static latency dashboard HTML (gated on DEBUG_DASHBOARD=1)."""
    if os.environ.get("DEBUG_DASHBOARD", "").strip() != "1":
        return web.Response(status=404)
    html = (Path(__file__).parent / "static" / "latency.html").read_text()
    return web.Response(text=html, content_type="text/html")


def build_app(backend_name: str) -> web.Application:
    """Build the aiohttp app: health + static client + internal endpoints."""
    client_dir = Path(__file__).parent.parent / "client"

    async def index(_request: web.Request) -> web.Response:
        return web.Response(
            text=(client_dir / "interface.html").read_text(),
            content_type="text/html",
        )

    async def static(request: web.Request) -> web.Response:
        fname = request.match_info["filename"]
        fpath = (client_dir / fname).resolve()
        if not fpath.is_relative_to(client_dir.resolve()):
            return web.Response(status=403)
        if not fpath.exists() or not fpath.is_file():
            return web.Response(status=404)
        ctype = "application/javascript" if fname.endswith(".js") else "text/plain"
        return web.Response(body=fpath.read_bytes(), content_type=ctype)

    async def health(request: web.Request) -> web.Response:
        token = os.environ.get("NEMO_APP_TOKEN", "").strip()
        auth = request.headers.get("Authorization", "")
        provided = auth[7:] if auth.startswith("Bearer ") else ""
        if token and hmac.compare_digest(provided, token):
            return web.json_response({**METRICS.snapshot(), "backend": backend_name})
        return web.json_response({"status": "ok"})

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/internal/brain_result", handle_brain_result)
    app.router.add_post("/internal/proactive", handle_proactive)
    app.router.add_get("/debug/latency", handle_debug_latency)
    app.router.add_get("/debug/latency.html", handle_latency_dashboard)
    app.router.add_get("/{filename}", static)
    return app


async def serve(port: int, ssl_ctx=None, backend_name: str = "unknown") -> None:
    """Start the HTTP server and run until cancelled."""
    app = build_app(backend_name)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port, ssl_context=ssl_ctx)
    await site.start()
    LOG.info("Voice HTTP%s server on port %d", "S" if ssl_ctx else "", port)
