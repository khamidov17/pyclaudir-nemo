"""``send_message`` — The agent's primary way to talk to humans."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field

from ..formatting import markdown_to_telegram_html
from ..secrets_scrubber import scrub
from ..security import SendRateLimiter
from ..transcript import log_outbound
from ..tts import speak_to_app
from .base import BaseTool, ToolResult, record_outbound

log = logging.getLogger(__name__)

_send_rate_limiter = SendRateLimiter(max_messages=20, window_seconds=60.0)

#: Telegram's hard limit on a single text message.
_TELEGRAM_TEXT_LIMIT = 4096


def _chunk_text(text: str, limit: int = _TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Split ``text`` into chunks of at most ``limit`` characters.

    Prefers ``\\n\\n`` (paragraph) over ``\\n`` (line) over space over hard-cut.
    Separators at a chosen boundary are consumed, never duplicated onto the
    next chunk. Empty input returns ``[""]`` so callers can treat the result
    as a non-empty list.

    Runs on raw (pre-markdown) text so each chunk can be converted to
    Telegram HTML independently without splitting an inline tag in half.
    Markdown constructs rarely span paragraph boundaries, so paragraph-
    preferring splits keep the rendered output intact.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        for sep in ("\n\n", "\n", " "):
            idx = window.rfind(sep)
            if idx > 0:
                chunks.append(remaining[:idx])
                next_start = idx + len(sep)
                # A ``\n\n`` straddling the window edge is only partially
                # visible to ``rfind("\n\n")`` — we split on the single
                # ``\n`` we saw, then consume any directly-adjacent ``\n``
                # so the next chunk doesn't lead with a separator.
                while next_start < len(remaining) and remaining[next_start] == "\n":
                    next_start += 1
                remaining = remaining[next_start:]
                break
        else:
            chunks.append(remaining[:limit])
            remaining = remaining[limit:]

    if remaining:
        chunks.append(remaining)
    return chunks


class SendMessageArgs(BaseModel):
    chat_id: int = Field(description="Telegram chat id.")
    text: str = Field(description="Message body. Plain text by default.")
    reply_to_message_id: int | None = Field(default=None)
    parse_mode: Literal["HTML", "MarkdownV2", None] = Field(default=None)


class SendMessageTool(BaseTool):
    name = "send_message"
    description = (
        "Send a text message to a Telegram chat. Long replies are split "
        "automatically at paragraph boundaries. Returns the first new "
        "message_id plus the full list in ``message_ids``."
    )
    args_model = SendMessageArgs

    async def run(self, args: SendMessageArgs) -> ToolResult:
        if self.ctx.bot is None:
            return ToolResult(content="bot not configured", is_error=True)

        scrubbed_text = scrub(args.text)
        if scrubbed_text != args.text:
            log.warning(
                "outbound message contained sensitive content — redacted before send"
            )

        if not _send_rate_limiter.check_and_record(args.chat_id):
            log.warning("outbound rate limit hit for chat=%s", args.chat_id)
            return ToolResult(
                content="rate limited: too many messages to this chat", is_error=True
            )

        # Chunk the RAW text before markdown conversion so each chunk's HTML
        # is self-contained (no mid-tag splits across chunk boundaries).
        raw_chunks = _chunk_text(scrubbed_text)
        parse_mode = args.parse_mode
        if parse_mode is None:
            bodies = [markdown_to_telegram_html(c) for c in raw_chunks]
            parse_mode = "HTML"
        else:
            bodies = list(raw_chunks)

        # Broadcast to app clients FIRST — independent of Telegram.
        # This ensures the app gets the reply even if Telegram send fails
        # (e.g. user never opened the Telegram bot DM).
        import json as _json
        if self.ctx.app_clients:
            payload = _json.dumps({"type": "message", "text": scrubbed_text, "chat_id": args.chat_id})
            dead: set = set()
            for ws in list(self.ctx.app_clients):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)
            self.ctx.app_clients -= dead
            if self.ctx.on_chat_replied is not None:
                try:
                    self.ctx.on_chat_replied(args.chat_id)
                except Exception:
                    pass
            # Generate and send TTS audio (fire-and-forget, non-blocking)
            asyncio.create_task(
                speak_to_app(scrubbed_text, self.ctx.app_clients),
                name="nemo-tts",
            )

        # App-originated turns: the app already got the reply via the
        # broadcast above. Don't echo it into the owner's Telegram DM (the
        # app submits with chat_id == owner_id, so a Telegram send here would
        # duplicate the message in Telegram). See ToolContext.app_origin_chats.
        if args.chat_id in getattr(self.ctx, "app_origin_chats", set()):
            return ToolResult(
                content="delivered to app (telegram suppressed for app-origin turn)",
                data={"chat_id": args.chat_id, "message_ids": []},
            )

        # Send to Telegram — may fail if user hasn't opened bot DM yet.
        message_ids: list[int] = []
        for i, body in enumerate(bodies):
            reply_to = args.reply_to_message_id if i == 0 else None
            try:
                sent = await self.ctx.bot.send_message(
                    chat_id=args.chat_id,
                    text=body,
                    reply_to_message_id=reply_to,
                    parse_mode=parse_mode,
                )
                message_ids.append(sent.message_id)
                log.info(
                    "hot-path stage=delivered chat=%s msg=%s chunk=%d/%d",
                    args.chat_id, sent.message_id, i + 1, len(bodies),
                )
                if i == 0 and self.ctx.on_chat_replied is not None:
                    try:
                        self.ctx.on_chat_replied(args.chat_id)
                    except Exception:
                        pass
            except Exception as exc:
                log.warning("telegram send failed chat=%s: %s", args.chat_id, exc)

        if not message_ids:
            # App clients already got it — return success anyway
            return ToolResult(
                content="delivered to app (telegram unavailable)",
                data={"chat_id": args.chat_id, "message_ids": []},
            )

        first_id = message_ids[0]
        log_outbound(
            chat_id=args.chat_id,
            chat_titles=self.ctx.chat_titles,
            message_id=first_id,
            reply_to_id=args.reply_to_message_id,
            text=scrubbed_text,
        )

        # Persist each delivered chunk as its own row. ``record_outbound``
        # internally handles the bot-identity lookup; PTB caches the
        # ``get_me`` result after the first call, so the N-1 follow-ups
        # cost a dict read.
        for i, (mid, raw_chunk) in enumerate(zip(message_ids, raw_chunks)):
            await record_outbound(
                self.ctx,
                chat_id=args.chat_id,
                message_id=mid,
                text=raw_chunk,
                reply_to_id=args.reply_to_message_id if i == 0 else None,
            )

        content = (
            f"sent message_id={first_id}"
            if len(message_ids) == 1
            else f"sent {len(message_ids)} chunks: message_ids={message_ids}"
        )
        return ToolResult(
            content=content,
            data={
                "message_id": first_id,
                "message_ids": message_ids,
                "chat_id": args.chat_id,
            },
        )
