"""Recall tools for recorded meetings.

The phone records a meeting and uploads it (``POST /recording/upload``); the
server transcribes it (Groq Whisper, ``stt.py``) and stores audio + transcript
under ``data/recordings/<id>/``. These tools let the engine brain actually
*reach* that transcript when the user later asks "summarize what we recorded" /
"send me the transcript" (which arrives via the voice path's ``delegate_task``).

Without these, the transcript is written but unreadable by the assistant — the
missing hop the recorder audit flagged.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import BaseTool, ToolResult

_MAX_TRANSCRIPT_CHARS = 12000


class ListRecordingsArgs(BaseModel):
    pass


class ListRecordingsTool(BaseTool):
    name = "list_recordings"
    description = (
        "List recorded meetings (newest first): id, date, duration, and whether "
        "transcription has finished. Use to find which recording the user means "
        "before reading its transcript."
    )
    args_model = ListRecordingsArgs

    async def run(self, args: ListRecordingsArgs) -> ToolResult:
        store = self.ctx.recording_store
        if store is None:
            return ToolResult(content="Recordings are not configured.", is_error=True)
        items = store.list()
        if not items:
            return ToolResult(content="No recordings yet.")
        lines = [
            f"- {m.id} · {m.date} · {m.duration_sec}s · "
            f"{'transcribed' if m.transcribed else 'transcribing…'}"
            + (f' · "{m.title}"' if m.title else "")
            for m in items
        ]
        return ToolResult(
            content="\n".join(lines),
            data={"count": len(items), "ids": [m.id for m in items]},
        )


class ReadTranscriptArgs(BaseModel):
    rec_id: str = Field(
        default="",
        description="Recording id to read. Leave empty for the most recent recording.",
    )


class ReadTranscriptTool(BaseTool):
    name = "read_transcript"
    description = (
        "Read a recorded meeting's transcript so you can summarize it, answer "
        "questions about it, or send it. Pass a rec_id from list_recordings, or "
        "leave it empty for the most recent recording."
    )
    args_model = ReadTranscriptArgs

    async def run(self, args: ReadTranscriptArgs) -> ToolResult:
        store = self.ctx.recording_store
        if store is None:
            return ToolResult(content="Recordings are not configured.", is_error=True)

        rec_id = (args.rec_id or "").strip()
        if not rec_id:
            latest = store.latest()
            if latest is None:
                return ToolResult(content="No recordings yet.")
            rec_id = latest.id

        try:
            meta = store.get(rec_id)
        except ValueError:
            # rec_id failed the store's path-safety guard.
            return ToolResult(content=f"Invalid recording id: {rec_id}", is_error=True)
        if meta is None:
            return ToolResult(content=f"No recording with id {rec_id}.", is_error=True)

        transcript = store.read_transcript(rec_id)
        if not transcript:
            return ToolResult(
                content=(
                    f"Recording {rec_id} ({meta.date}) isn't transcribed yet — "
                    "try again shortly."
                )
            )
        truncated = len(transcript) > _MAX_TRANSCRIPT_CHARS
        body = transcript[:_MAX_TRANSCRIPT_CHARS]
        if truncated:
            body += "\n\n[transcript truncated]"
        header = f"Transcript of {rec_id} ({meta.date}, {meta.duration_sec}s):\n\n"
        return ToolResult(
            content=header + body,
            data={
                "id": rec_id,
                "transcribed": meta.transcribed,
                "truncated": truncated,
            },
        )
