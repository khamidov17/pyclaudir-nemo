"""Entrypoint: ``python -m pyclaudir``.

Brings up the four components in order:

1. SQLite database (with migrations applied)
2. Local MCP server on a random localhost port
3. Claude Code subprocess via the CC worker
4. Engine + Telegram dispatcher

Then sleeps until interrupted, at which point everything is torn down.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .access import AccessConfig, load_access, save_access
from .storage.attachments import AttachmentStore
from .cc_schema import schema_json
from .cc_worker import CORE_ALLOWED_TOOLS, CcSpawnSpec, CcWorker
from .config import Config
from .db.database import Database
from .db.messages import insert_tool_call
from .db.reminders import (
    advance_recurring_reminder,
    any_with_auto_seed_key,
    fetch_due_reminders,
    insert_auto_seeded_reminder,
    mark_reminder_sent,
    pending_with_auto_seed_key,
)
from .engine import Engine
from .instructions_store import InstructionsStore
from .app_api import AppApiServer
from .mcp_server import McpServer
from .phone_broker import PhoneBroker
from .nemo_router import NemoRouter, RouterConfig
from .storage.memory import MemoryStore
from .plugins import Plugins, load_plugins
from .rate_limiter import RateLimiter
from .storage.render import RenderStore
from .skills_store import SkillsStore
from .telegram_io import TelegramDispatcher
from .tools.base import ToolContext

log = logging.getLogger("pyclaudir")


def _setup_logging() -> None:
    """Configure logging so the transcript is the star.

    The ``pyclaudir.tx`` logger emits one line per inbound/outbound/edit/
    delete/reaction message, prefixed ``[RX]`` / ``[TX]`` / etc. We quiet
    down the high-volume HTTP polling chatter so those lines stand out.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx prints one INFO line per long-poll getUpdates (every ~10s).
    # That spam buries the actual conversation. Silence everything below
    # WARNING for it.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # MCP per-request logs are interesting when debugging tool calls but
    # noisy in normal operation. Comment this out if you want them back.
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)
    logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.WARNING)


_SELF_REFLECTION_KEY = "self-reflection-default"
_PROFILE_SYNTHESIS_KEY = "profile-synthesis-default"
_MEMORY_CONSOLIDATION_KEY = "memory-consolidation-default"


async def _seed_default_reminders(db, config) -> None:
    """Ensure the default self-reflection reminder is active.

    The self-reflection loop is **mandatory** — the bot shouldn't be
    able to stop learning. On every startup we check whether a PENDING
    row with ``auto_seed_key='self-reflection-default'`` exists. If
    not (missing entirely, cancelled, deleted, whatever the reason),
    we re-seed. Cancellation is also blocked at the tool layer — see
    ``CancelReminderTool`` — so this is defense in depth against DB
    tampering or manual SQL.
    """
    existing = await pending_with_auto_seed_key(db, _SELF_REFLECTION_KEY)
    if existing > 0:
        log.info(
            "self-reflection reminder: %d pending row(s) active, skipping seed",
            existing,
        )
    else:
        cron_expr = config.self_reflection_cron
        # Compute the first trigger time from the cron expression if croniter
        # is available; otherwise default to "now" so the reminder loop will
        # pick it up immediately.
        first_trigger = datetime.now(timezone.utc)
        try:
            from croniter import croniter

            first_trigger = croniter(cron_expr, first_trigger).get_next(datetime)
        except ImportError:  # pragma: no cover
            log.warning(
                "croniter not installed, self-reflection reminder set to trigger now"
            )

        await insert_auto_seeded_reminder(
            db,
            auto_seed_key=_SELF_REFLECTION_KEY,
            chat_id=config.owner_id,
            user_id=-1,  # synthetic pseudo-user (same convention as reminder loop)
            text='<skill name="self-reflection">run</skill>',
            trigger_at=first_trigger.strftime("%Y-%m-%d %H:%M:%S"),
            cron_expr=cron_expr,
        )
        log.info(
            "seeded default self-reflection reminder (cron=%s, next=%s UTC)",
            cron_expr,
            first_trigger.strftime("%Y-%m-%d %H:%M:%S"),
        )

    # Always check these — re-seeds if deleted between restarts
    await _seed_profile_synthesis_reminder(db, config)
    await _seed_memory_consolidation_reminder(db, config)
    await _seed_briefing_reminders(db, config)


# Proactive spoken briefings. Local times converted to UTC cron using
# NEMO_UTC_OFFSET (Tashkent = +5). Overridable via env. Unlike the mandatory
# loops these are seeded ONCE (any-status check) so a user who cancels one
# keeps it off. The text fires into the engine, which speaks it on the phone.
_BRIEFINGS = (
    (
        "morning-brief-default",
        "NEMO_MORNING_BRIEF_CRON",
        "0 3 * * *",  # 08:00 Tashkent
        "Good-morning briefing for Avazbek — speak it warmly like a friend, 2-3 "
        "sentences: greet him by name, today's date and time, anything you "
        "remember he's working on or has coming up, and one upbeat nudge for the "
        "day. Use send_message so it's spoken on his phone.",
    ),
    (
        "evening-brief-default",
        "NEMO_EVENING_BRIEF_CRON",
        "0 16 * * *",  # 21:00 Tashkent
        "Evening check-in for Avazbek — speak it warmly, 2-3 sentences: ask how "
        "his day went, recap anything notable he told you today, and remind him "
        "of anything pending for tomorrow. Use send_message so it's spoken on "
        "his phone.",
    ),
)


async def _seed_briefing_reminders(db, config) -> None:
    """Install the morning + evening spoken briefings, once each.

    OFF by default — all scheduling is user-driven (the owner sets/cancels
    briefings and reminders by voice). Set NEMO_SEED_BRIEFINGS=1 to opt back
    into the two default briefings.
    """
    import os

    if os.environ.get("NEMO_SEED_BRIEFINGS") != "1":
        return
    for key, env, default_cron, text in _BRIEFINGS:
        if await any_with_auto_seed_key(db, key) > 0:
            continue
        cron_expr = os.environ.get(env, default_cron)
        first_trigger = datetime.now(timezone.utc)
        try:
            from croniter import croniter

            first_trigger = croniter(cron_expr, first_trigger).get_next(datetime)
        except ImportError:  # pragma: no cover
            pass
        await insert_auto_seeded_reminder(
            db,
            auto_seed_key=key,
            chat_id=config.owner_id,
            user_id=-1,
            text=text,
            trigger_at=first_trigger.strftime("%Y-%m-%d %H:%M:%S"),
            cron_expr=cron_expr,
        )
        log.info("seeded %s (cron=%s, next=%s UTC)", key, cron_expr, first_trigger)


async def _seed_profile_synthesis_reminder(db, config) -> None:
    """Seed the daily profile synthesis reminder (ABOUT_ME.md auto-refresh).

    Runs at 23:59 Tashkent (18:59 UTC) by default. Re-seeded on every startup if missing,
    same pattern as the self-reflection reminder.
    """
    existing = await pending_with_auto_seed_key(db, _PROFILE_SYNTHESIS_KEY)
    if existing > 0:
        log.info("profile-synthesis reminder: %d active, skipping seed", existing)
        return

    cron_expr = config.profile_synthesis_cron
    first_trigger = datetime.now(timezone.utc)
    try:
        from croniter import croniter

        first_trigger = croniter(cron_expr, first_trigger).get_next(datetime)
    except ImportError:  # pragma: no cover
        pass

    await insert_auto_seeded_reminder(
        db,
        auto_seed_key=_PROFILE_SYNTHESIS_KEY,
        chat_id=config.owner_id,
        user_id=-1,
        text=(
            "Daily profile refresh: call synthesize_memory_wiki to read all memories, "
            "then write or update ABOUT_ME.md with a comprehensive, concise profile. "
            "Sections: Identity, Current Projects, Preferences, Relationships, Context."
        ),
        trigger_at=first_trigger.strftime("%Y-%m-%d %H:%M:%S"),
        cron_expr=cron_expr,
    )
    log.info(
        "seeded profile-synthesis reminder (cron=%s, next=%s UTC)",
        cron_expr,
        first_trigger.strftime("%Y-%m-%d %H:%M:%S"),
    )


async def _seed_memory_consolidation_reminder(db, config) -> None:
    """Seed the weekly memory consolidation reminder.

    Runs every Sunday at 20:00 UTC (Monday 01:00 Tashkent). Re-seeded on
    every startup if missing. The agent merges scattered memory files,
    removes duplicates, and keeps the memory store clean.
    """
    existing = await pending_with_auto_seed_key(db, _MEMORY_CONSOLIDATION_KEY)
    if existing > 0:
        log.info("memory-consolidation reminder: %d active, skipping seed", existing)
        return

    cron_expr = "0 20 * * 0"  # Sunday 20:00 UTC = Monday 01:00 Tashkent
    first_trigger = datetime.now(timezone.utc)
    try:
        from croniter import croniter

        first_trigger = croniter(cron_expr, first_trigger).get_next(datetime)
    except ImportError:  # pragma: no cover
        pass

    await insert_auto_seeded_reminder(
        db,
        auto_seed_key=_MEMORY_CONSOLIDATION_KEY,
        chat_id=config.owner_id,
        user_id=-1,
        text=(
            "Weekly memory consolidation: list all memory files, read each one, "
            "merge files with overlapping content, remove duplicates, fix stale facts. "
            "Then refresh ABOUT_ME.md. Keep every file concise and non-redundant."
        ),
        trigger_at=first_trigger.strftime("%Y-%m-%d %H:%M:%S"),
        cron_expr=cron_expr,
    )
    log.info("seeded memory-consolidation reminder (next=%s UTC)", first_trigger)


def _bootstrap_access(config: Config) -> None:
    """First-run access.json seed, then log the resolved policy.

    Default is owner-only DMs with no allowed chats — operator adds
    others later via ``/telegram:access``.
    """
    if not config.access_path.exists():
        seed = AccessConfig(policy="owner_only", allowed_users=[], allowed_chats=[])
        save_access(config.access_path, seed)
        log.info("created %s (policy=owner_only, chats=[])", config.access_path)
        return
    access = load_access(config.access_path)
    log.info(
        "access: policy=%s, allowed_users=%d, allowed_chats=%d",
        access.policy,
        len(access.allowed_users),
        len(access.allowed_chats),
    )


@dataclass
class _Stores:
    """Bundle of long-lived stores constructed at startup. Keeps the
    bootstrap pipeline's signature manageable — the stores are read-only
    after construction and shared across MCP tools, dispatcher, engine."""

    memory: MemoryStore
    instructions: InstructionsStore
    skills: SkillsStore
    attachments: AttachmentStore
    renders: RenderStore
    rate_limiter: RateLimiter


def _build_stores(config: Config, db: Database, plugins: Plugins) -> _Stores:
    """Construct + warm every disk-backed store."""
    project_root = Path(__file__).resolve().parent.parent
    memory = MemoryStore(config.memories_dir)
    memory.ensure_root()
    instructions = InstructionsStore(
        project_md_path=config.project_prompt_path,
        backup_dir=config.data_dir / "prompt_backups",
    )
    instructions.ensure_dirs()
    skills = SkillsStore(
        root=project_root / "skills",
        disabled=plugins.skills_disabled,
    )
    skills.ensure_root()
    attachments = AttachmentStore(config.attachments_dir)
    renders = RenderStore(config.renders_dir)
    renders.ensure_root()
    rate_limiter = RateLimiter(
        db=db,
        limit=config.rate_limit_per_min,
        owner_id=config.owner_id,
    )
    return _Stores(
        memory=memory,
        instructions=instructions,
        skills=skills,
        attachments=attachments,
        renders=renders,
        rate_limiter=rate_limiter,
    )


def _build_external_mcp_config(
    plugins: Plugins,
) -> tuple[dict, list[str]]:
    """Build the MCP-server map + allowed-tool list for the CC subprocess.

    Each enabled entry in ``plugins.json`` whose ``${VAR}`` references all
    resolved contributes one server here. The plugin's ``name`` is the
    dict key Claude Code uses to namespace tools as ``mcp__<name>__<tool>``
    — those names are load-bearing and visible to the model.
    """
    extra_mcp: dict = {}
    mcp_allowed_tools: list[str] = []
    for plugin in plugins.mcps:
        if plugin.type == "stdio":
            extra_mcp[plugin.name] = {
                "type": "stdio",
                "command": plugin.command,
                "args": list(plugin.args),
                "env": dict(plugin.env),
            }
            log.info(
                "mcp %s configured (type=stdio, command=%s)",
                plugin.name,
                plugin.command,
            )
        else:  # http or sse — remote server, optional static auth headers
            entry: dict = {"type": plugin.type, "url": plugin.url}
            if plugin.headers:
                entry["headers"] = dict(plugin.headers)
            extra_mcp[plugin.name] = entry
            log.info(
                "mcp %s configured (type=%s, url=%s)",
                plugin.name,
                plugin.type,
                plugin.url,
            )
        mcp_allowed_tools.extend(plugin.allowed_tools)
    return extra_mcp, mcp_allowed_tools


def _load_session_id(config: Config) -> str | None:
    """Resume the prior CC session if one was persisted on a clean shutdown."""
    if not config.session_id_path.exists():
        return None
    session_id = config.session_id_path.read_text().strip() or None
    if session_id:
        log.info("resuming cc session %s", session_id)
    return session_id


async def _advance_or_close_reminder(db: Database, row: dict) -> None:
    """For a fired reminder: advance the cron schedule if recurring,
    otherwise mark it sent so it doesn't fire again."""
    cron_expr = row["cron_expr"]
    if not cron_expr:
        await mark_reminder_sent(db, row["id"])
        return
    try:
        from croniter import croniter

        next_dt = croniter(
            cron_expr,
            datetime.now(timezone.utc),
        ).get_next(datetime)
        await advance_recurring_reminder(
            db,
            row["id"],
            next_dt.strftime("%Y-%m-%d %H:%M:%S"),
        )
    except ImportError:
        log.warning(
            "croniter not installed, marking cron reminder #%d as sent",
            row["id"],
        )
        await mark_reminder_sent(db, row["id"])


def _make_reminder_on_success(db: Database, row: dict):
    """Build the engine ``on_success`` hook that commits a reminder
    once CC has actually processed the turn (#22)."""

    async def _on_success() -> None:
        await _advance_or_close_reminder(db, row)
        log.info("delivered reminder #%d", row["id"])

    return _on_success


async def _fire_one_reminder(db: Database, engine: Engine, row: dict) -> None:
    """Inject one due reminder into the engine as a synthetic message.

    The schedule advance / one-shot close is deferred to an
    ``on_success`` callback the engine fires after CC actually consumes
    the turn. If the CC subprocess crashes or wedges before processing
    the reminder XML, the callback never fires and the row stays
    ``pending`` — the next 60s reminder loop tick re-fires it. Without
    this, a wedged subprocess would silently drop the reminder (#22).

    If the engine is mid-turn when the reminder fires, the synthetic
    message goes into the pending buffer and runs after the current
    turn ends.
    """
    from .models import ChatMessage

    reminder_xml = (
        f'<reminder id="{row["id"]}" chat_id="{row["chat_id"]}" '
        f'user_id="{row["user_id"]}">{row["text"]}</reminder>'
    )
    await engine.submit(
        ChatMessage(
            chat_id=row["chat_id"],
            message_id=0,
            user_id=row["user_id"],
            direction="in",
            timestamp=datetime.now(timezone.utc),
            text=reminder_xml,
            source="reminder",  # scheduler turn → blocks autonomous reads
        ),
        on_success=_make_reminder_on_success(db, row),
    )


async def _reminder_loop(db: Database, engine: Engine) -> None:
    """Background reminder scheduler — polls for due reminders and injects
    them into the engine as synthetic inbound messages.

    The poll interval (``NEMO_REMINDER_POLL_SEC``, default 10s) also bounds how
    fast a voice ``delegate_task`` reaches the engine — it lands as an immediate
    reminder, so a slow poll made delegated coding feel broken ("taking too
    long"). 10s keeps delegated tasks snappy while staying a cheap indexed query.

    Reminders fire unconditionally when due. If the engine is mid-turn
    the synthetic message gets buffered and runs after the current
    turn ends. Each reminder is fired in its own try/except so a single
    failure (DB error, submit blow-up) doesn't block subsequent
    reminders in the same poll cycle, and the failing row's id is
    logged so it's easy to track down.
    """
    poll_sec = int(os.environ.get("NEMO_REMINDER_POLL_SEC", "10"))
    while True:
        # Wake on a kick (a just-delegated task) or after poll_sec at the latest.
        try:
            await asyncio.wait_for(engine.reminder_kick.wait(), timeout=poll_sec)
        except asyncio.TimeoutError:
            pass
        engine.reminder_kick.clear()
        try:
            now_dt = datetime.now(timezone.utc)
            due = await fetch_due_reminders(
                db,
                now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception:
            log.exception("reminder loop: fetch_due_reminders failed")
            continue
        if not due:
            log.debug("reminder loop: no due reminders")
            continue
        log.info("reminder loop: %d due", len(due))
        for row in due:
            try:
                log.info(
                    "firing reminder #%d (chat=%s)",
                    row["id"],
                    row["chat_id"],
                )
                await _fire_one_reminder(db, engine, row)
                # NB: not "fired" — the row stays ``pending`` until the
                # engine's on_success callback runs after CC processes
                # the turn. See ``_fire_one_reminder`` for the rationale.
                log.info("queued reminder #%d", row["id"])
            except Exception:
                log.exception("failed to fire reminder #%d", row["id"])


def _install_signal_handlers(
    worker: CcWorker,
    stop_event: asyncio.Event,
) -> None:
    """Wire SIGINT/SIGTERM to the same stop path. Tells the cc supervisor
    we're shutting down BEFORE it observes the subprocess exit (the SIGINT
    propagates to the same process group, so cc is exiting in parallel).
    Without this the supervisor treats the clean exit as a crash and
    respawns."""

    def _stop(*_a) -> None:
        log.info("signal received, shutting down")
        worker._stop_supervisor.set()
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)


async def _async_main() -> None:
    _setup_logging()

    config = Config.from_env()
    config.ensure_dirs()

    from pyclaudir.security import kill_marker_exists

    if kill_marker_exists(config.data_dir):
        logging.getLogger(__name__).warning(
            "kill_marker present — refusing to start. Remove %s/kill_marker to restart.",
            config.data_dir,
        )
        sys.exit(0)

    _bootstrap_access(config)

    db = await Database.open(config.db_path)
    log.info("database ready at %s", config.db_path)

    project_root = Path(__file__).resolve().parent.parent
    plugins = load_plugins(project_root / "plugins.json")
    log.info(
        "plugins loaded: %d enabled mcp(s), %d disabled skill(s), "
        "%d disabled built-in tool(s), tool_groups=%s",
        len(plugins.mcps),
        len(plugins.skills_disabled),
        len(plugins.builtin_tools_disabled),
        dict(plugins.tool_groups),
    )

    stores = _build_stores(config, db, plugins)
    await _seed_default_reminders(db, config)

    async def db_logger(**kwargs):  # called by every MCP tool wrapper
        await insert_tool_call(db, **kwargs)

    # Shared between dispatcher (writer) and outbound tools (reader).
    chat_titles: dict[int, str] = {}
    ctx = ToolContext(
        bot=None,  # filled in below once dispatcher exists
        database=db,
        memory_store=stores.memory,
        instructions_store=stores.instructions,
        skills_store=stores.skills,
        attachment_store=stores.attachments,
        render_store=stores.renders,
        chat_titles=chat_titles,
    )

    mcp = McpServer(
        ctx,
        db_logger=db_logger,
        disabled=plugins.builtin_tools_disabled,
    )
    await mcp.start()
    log.info("mcp server live at %s", mcp.url)

    # Mobile app WebSocket bridge
    import os

    app_token = os.environ.get("NEMO_APP_TOKEN", "") or ""
    app_port = int(os.environ.get("NEMO_APP_PORT", "8765") or "8765")
    app_api: AppApiServer | None = None
    if app_token:
        broker = PhoneBroker(data_dir=config.data_dir)
        ctx.phone_broker = broker
        app_api = AppApiServer(
            token=app_token,
            owner_id=config.owner_id,
            ctx=ctx,
            broker=broker,
            data_dir=config.data_dir,
        )
        await app_api.start(port=app_port)
    elif config.app_only:
        log.warning(
            "app-only mode (no Telegram) but NEMO_APP_TOKEN is unset — "
            "Nemo has NO user interface. Set NEMO_APP_TOKEN to use the app."
        )
    else:
        log.info("NEMO_APP_TOKEN not set — mobile app bridge disabled")

    tmpdir = Path(tempfile.mkdtemp(prefix="pyclaudir-"))
    schema_path = tmpdir / "schema.json"
    schema_path.write_text(schema_json())
    extra_mcp, mcp_allowed_tools = _build_external_mcp_config(plugins)
    mcp_config_path = mcp.write_mcp_config(
        tmpdir / "mcp.json",
        extra_servers=extra_mcp,
    )
    log.info("mcp config written to %s", mcp_config_path)

    # Tool-group toggles flow through ``plugins.json`` exclusively —
    # edit the file and restart to flip.
    spec = CcSpawnSpec(
        binary=config.claude_code_bin,
        model=config.model,
        system_prompt_path=config.system_prompt_path,
        project_prompt_path=config.project_prompt_path,
        mcp_config_path=mcp_config_path,
        json_schema_path=schema_path,
        effort=config.effort,
        session_id=_load_session_id(config),
        cc_logs_dir=config.cc_logs_dir,
        enable_subagents=bool(plugins.tool_groups.get("subagents", False)),
        subagents_prompt_path=Path("prompts/subagents.md").resolve(),
        enable_bash=bool(plugins.tool_groups.get("bash", False)),
        enable_code=bool(plugins.tool_groups.get("code", False)),
        base_allowed_tools=CORE_ALLOWED_TOOLS,
        mcp_allowed_tools=(),
    )

    # Crash-callback closures reference ``engine`` / ``dispatcher`` via late
    # binding; the worker only invokes them after both are built.
    async def _notify_owner(text: str, chat_id: int | None = None) -> None:
        """Reach the user with a system notice — Telegram when a bot exists,
        else broadcast over the app/desktop WebSocket (app-only mode)."""
        target = config.owner_id if chat_id is None else chat_id
        if dispatcher is not None and dispatcher.bot is not None:
            try:
                await dispatcher.bot.send_message(chat_id=target, text=text)
            except Exception:
                log.warning("owner notify (telegram) failed", exc_info=True)
            return
        from .app_notify import broadcast_to_app

        await broadcast_to_app(ctx.app_clients, text, target)

    async def _on_cc_crash(attempt: int, backoff: float) -> None:
        user_text = (
            f"⚠️ Technical issue, restarting "
            f"(attempt {attempt}, retrying in {backoff:.0f}s). "
            "Please resend your last message in a moment."
        )
        active = engine._turn.active_chats if engine else set()
        for chat_id in active:
            await _notify_owner(user_text, chat_id)
        if config.owner_id not in active:
            await _notify_owner(
                f"CC error (attempt {attempt}). Check logs.", config.owner_id
            )

    async def _on_cc_stale_session(stale_id: str) -> None:
        try:
            config.session_id_path.unlink(missing_ok=True)
        except OSError:
            log.exception("failed to delete stale session_id file")
        await _notify_owner(
            "ℹ️ Previous Claude Code session expired — starting a fresh one. "
            "Your last message may need to be resent."
        )

    async def _on_cc_giveup(crash_count: int) -> None:
        user_text = (
            f"⚠️ Shutting down — Claude Code failed {crash_count} times. "
            "The operator needs to intervene."
        )
        chats_to_notify: set[int] = set(engine._turn.active_chats if engine else set())
        chats_to_notify.add(config.owner_id)
        for chat_id in chats_to_notify:
            await _notify_owner(user_text, chat_id)

    # Engine is declared here but constructed after dispatcher.
    engine = None  # type: ignore[assignment]

    worker = CcWorker(
        spec,
        config,
        heartbeat=ctx.heartbeat,
        on_crash=_on_cc_crash,
        on_giveup=_on_cc_giveup,
        on_stale_session=_on_cc_stale_session,
    )
    await worker.start()
    await worker.supervise()

    # The dispatcher owns the Telegram bot. In app-only mode (no token) we skip
    # it entirely — the phone app is the sole interface and inbound/outbound
    # both flow through app_api + send_message's app broadcast.
    dispatcher = None
    if not config.app_only:
        dispatcher = TelegramDispatcher(  # type: ignore[arg-type]
            config,
            db,
            engine=None,
            chat_titles=chat_titles,
            rate_limiter=stores.rate_limiter,
            memory_store=stores.memory,
            external_mcp_tools=tuple(mcp_allowed_tools),
            router=NemoRouter(
                RouterConfig(
                    enabled=config.router_enabled,
                    claude_bin=config.claude_code_bin,
                    model=config.router_model,
                    direct_replies=config.router_direct_replies,
                )
            ),
        )

    async def _typing(chat_id: int) -> None:
        # Typing indicator is a Telegram concept; the app shows its own
        # "thinking" state. No-op in app-only mode.
        if dispatcher is None or dispatcher.bot is None:
            return
        t0 = time.monotonic()
        try:
            ok = await dispatcher.bot.send_chat_action(chat_id=chat_id, action="typing")
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            log.debug(
                "send_chat_action chat=%s returned=%r elapsed=%dms",
                chat_id,
                ok,
                elapsed_ms,
            )
        except Exception as exc:
            log.warning("send_chat_action failed for chat %s: %s", chat_id, exc)

    async def _error_notify(chat_id: int, text: str) -> None:
        await _notify_owner(text, chat_id)

    from .models import ChatMessage
    from .tool_groups import build_turn_tools

    def _default_runtime_profile(cm: ChatMessage) -> dict:
        """Tool set for autonomous turns (reminder/webhook/app) that arrive with
        no routed profile — owner-gated so a non-owner turn never gets the
        OWNER_ONLY tools (run_code / phone / SQL), and so a turn can't inherit
        the previous turn's tools."""
        return {
            "model": None,
            "mcp_allowed_tools": build_turn_tools(
                cm.text,
                external_tools=tuple(mcp_allowed_tools),
                is_owner=(cm.user_id == config.owner_id),
            ),
        }

    engine = Engine(
        worker,
        config,
        debounce_ms=config.debounce_ms,
        db=db,
        typing_action=_typing,
        error_notify=_error_notify,
        ctx=ctx,
        default_runtime_profile=_default_runtime_profile,
    )
    await engine.start()
    if app_api is not None:
        app_api.set_engine(engine)

    reminder_task = asyncio.create_task(
        _reminder_loop(db, engine),
        name="pyclaudir-reminders",
    )

    if dispatcher is not None:
        dispatcher.engine = engine
        ctx.bot = dispatcher.bot
    # Wire send_message → engine notification so the typing indicator
    # stops the moment the user has the message in their hand, not when
    # the entire CC turn officially ends.
    ctx.on_chat_replied = engine.notify_chat_replied
    if dispatcher is not None:
        await dispatcher.start()
    log.info(
        "pyclaudir is live (%s)", "app-only" if config.app_only else "telegram+app"
    )

    stop_event = asyncio.Event()
    _install_signal_handlers(worker, stop_event)

    try:
        await stop_event.wait()
    finally:
        # Persist session id, then tear everything down in the order
        # opposite to construction. Clean shutdown — _stop already set.
        if worker.session_id:
            config.session_id_path.write_text(worker.session_id)
        reminder_task.cancel()
        if dispatcher is not None:
            await dispatcher.stop()
            if dispatcher.router is not None:
                await dispatcher.router.close()
        await engine.stop()
        await worker.stop()
        if app_api is not None:
            await app_api.stop()
        await mcp.stop()
        await db.close()
        log.info("clean shutdown complete")


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
