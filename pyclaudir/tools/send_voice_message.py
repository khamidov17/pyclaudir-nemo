"""send_voice_message — convert text to speech and send as Telegram voice.

Delegates all audio generation (Gemini TTS → OGG / Edge TTS fallback)
to pyclaudir.tts — no subprocess or file I/O inside this tool module.
"""

from __future__ import annotations

import io
import logging

from pydantic import BaseModel, Field

from ..secrets_scrubber import scrub
from ..tts import generate_ogg
from .base import BaseTool, ToolResult, record_outbound

log = logging.getLogger(__name__)


class SendVoiceArgs(BaseModel):
    chat_id: int = Field(description="Telegram chat id.")
    text: str = Field(description="Text to speak aloud as a Telegram voice message.")
    reply_to_message_id: int | None = Field(default=None)


class SendVoiceMessageTool(BaseTool):
    name = "send_voice_message"
    description = (
        "Convert text to speech and send as a Telegram voice message using "
        "Gemini TTS (natural voice). Use for greetings, fun moments, or when "
        "the user asks for a voice reply."
    )
    args_model = SendVoiceArgs

    async def run(self, args: SendVoiceArgs) -> ToolResult:
        if self.ctx.bot is None:
            return ToolResult(content="bot not configured", is_error=True)
        if not args.text.strip():
            return ToolResult(content="empty text", is_error=True)

        text = scrub(args.text)

        try:
            ogg_bytes = await generate_ogg(text)
        except Exception as exc:
            log.warning("TTS generation failed: %s", exc)
            return ToolResult(content=f"TTS failed: {exc}", is_error=True)

        try:
            sent = await self.ctx.bot.send_voice(
                chat_id=args.chat_id,
                voice=io.BytesIO(ogg_bytes),
                reply_to_message_id=args.reply_to_message_id,
            )
        except Exception as exc:
            log.warning("send_voice failed: %s", exc)
            return ToolResult(content=f"send failed: {exc}", is_error=True)

        log.info("voice message sent chat=%s msg=%s", args.chat_id, sent.message_id)

        if self.ctx.on_chat_replied is not None:
            try:
                self.ctx.on_chat_replied(args.chat_id)
            except Exception:
                pass

        await record_outbound(
            self.ctx,
            chat_id=args.chat_id,
            message_id=sent.message_id,
            text=f"[voice] {text[:80]}",
            reply_to_id=args.reply_to_message_id,
        )
        return ToolResult(
            content=f"voice message sent message_id={sent.message_id}",
            data={"message_id": sent.message_id, "chat_id": args.chat_id},
        )
