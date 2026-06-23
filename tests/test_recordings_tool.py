"""Engine recall tools for recorded meetings (the missing hop)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyclaudir.recording_store import RecordingStore, SaveOpts
from pyclaudir.tools.base import ToolContext
from pyclaudir.tools.recordings import (
    ListRecordingsArgs,
    ListRecordingsTool,
    ReadTranscriptArgs,
    ReadTranscriptTool,
)


def _ctx_with_store(tmp_path: Path) -> tuple[ToolContext, RecordingStore]:
    store = RecordingStore(tmp_path / "recordings")
    return ToolContext(recording_store=store), store


async def test_read_transcript_latest_when_no_id(tmp_path: Path) -> None:
    ctx, store = _ctx_with_store(tmp_path)
    store.save_audio("rec-1", b"a", SaveOpts("a.m4a", 1_000_000, 1_030_000))
    store.set_transcript("rec-1", "we agreed to ship friday")
    out = await ReadTranscriptTool(ctx).run(ReadTranscriptArgs())
    assert not out.is_error
    assert "ship friday" in out.content
    assert out.data["id"] == "rec-1"


async def test_read_transcript_pending(tmp_path: Path) -> None:
    ctx, store = _ctx_with_store(tmp_path)
    store.save_audio("rec-1", b"a", SaveOpts("a.m4a", 0, 1000))  # no transcript yet
    out = await ReadTranscriptTool(ctx).run(ReadTranscriptArgs(rec_id="rec-1"))
    assert "isn't transcribed yet" in out.content


async def test_read_transcript_unknown_id(tmp_path: Path) -> None:
    ctx, _ = _ctx_with_store(tmp_path)
    out = await ReadTranscriptTool(ctx).run(ReadTranscriptArgs(rec_id="nope"))
    assert out.is_error and "No recording" in out.content


async def test_read_transcript_rejects_traversal_id(tmp_path: Path) -> None:
    ctx, _ = _ctx_with_store(tmp_path)
    out = await ReadTranscriptTool(ctx).run(ReadTranscriptArgs(rec_id="../../etc"))
    assert out.is_error and "Invalid recording id" in out.content


async def test_list_recordings(tmp_path: Path) -> None:
    ctx, store = _ctx_with_store(tmp_path)
    store.save_audio("rec-1", b"a", SaveOpts("a.m4a", 1_000_000, 1_065_000))
    store.set_transcript("rec-1", "hello")
    out = await ListRecordingsTool(ctx).run(ListRecordingsArgs())
    assert "rec-1" in out.content and out.data["count"] == 1


async def test_tools_degrade_without_store() -> None:
    ctx = ToolContext()  # no recording_store (e.g. data_dir unset)
    r1 = await ListRecordingsTool(ctx).run(ListRecordingsArgs())
    r2 = await ReadTranscriptTool(ctx).run(ReadTranscriptArgs())
    assert r1.is_error and r2.is_error


@pytest.mark.parametrize("empty_call", [True, False])
async def test_no_recordings_message(tmp_path: Path, empty_call: bool) -> None:
    ctx, _ = _ctx_with_store(tmp_path)
    if empty_call:
        out = await ListRecordingsTool(ctx).run(ListRecordingsArgs())
    else:
        out = await ReadTranscriptTool(ctx).run(ReadTranscriptArgs())
    assert "No recordings yet" in out.content
