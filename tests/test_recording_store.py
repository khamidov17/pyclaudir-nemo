"""Recording store + /recording/upload endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import pytest

from pyclaudir.app_api import AppApiServer
from pyclaudir.phone_broker import PhoneBroker
from pyclaudir.recording_store import RecordingStore, SaveOpts, is_safe_rec_id
from pyclaudir.tools.base import ToolContext


def test_store_roundtrip_and_dated(tmp_path: Path) -> None:
    store = RecordingStore(tmp_path)
    meta = store.save_audio(
        "rec-1", b"abc", SaveOpts("meeting.m4a", 1_000_000, 1_065_000)
    )
    assert meta.audio_bytes == 3
    assert meta.duration_sec == 65
    assert meta.date  # YYYY-MM-DD
    assert not meta.transcribed
    store.set_transcript("rec-1", "hello team")
    assert store.read_transcript("rec-1") == "hello team"
    assert store.get("rec-1").transcribed is True
    assert store.latest().id == "rec-1"


def test_store_evicts_oldest_past_count_cap(tmp_path: Path, monkeypatch) -> None:
    import pyclaudir.recording_store as rs

    monkeypatch.setattr(rs, "_MAX_RECORDINGS", 2)
    store = RecordingStore(tmp_path)
    for i in range(4):
        store.save_audio(f"rec-{i}", b"x", SaveOpts("a.m4a", i * 1000, i * 1000 + 500))
    ids = {m.id for m in store.list()}
    assert ids == {"rec-2", "rec-3"}  # two oldest evicted


def _server(tmp_path: Path) -> AppApiServer:
    ctx = ToolContext.__new__(ToolContext)
    ctx.app_clients = set()
    broker = PhoneBroker(data_dir=tmp_path)
    return AppApiServer("tok", 1, ctx, broker, data_dir=tmp_path)


def test_upload_requires_auth(tmp_path: Path) -> None:
    client = TestClient(_server(tmp_path).app)
    resp = client.post(
        "/recording/upload",
        data={"id": "r1", "started_ms": "0", "ended_ms": "1000", "token": "wrong"},
        files={"file": ("a.m4a", b"audio", "audio/mp4")},
    )
    assert resp.status_code == 401


def test_upload_saves_and_returns_fast(tmp_path: Path) -> None:
    client = TestClient(_server(tmp_path).app)
    resp = client.post(
        "/recording/upload",
        data={"id": "r1", "started_ms": "0", "ended_ms": "2000", "token": "tok"},
        files={"file": ("a.m4a", b"audiobytes", "audio/mp4")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "r1" and body["bytes"] == 10
    assert (tmp_path / "recordings" / "r1" / "a.m4a").read_bytes() == b"audiobytes"


@pytest.mark.parametrize(
    "bad_id",
    ["../evil", "../../etc/x", "a/b", "..", ".", "with space", "semi;colon", ""],
)
def test_store_rejects_traversal_ids(tmp_path: Path, bad_id: str) -> None:
    store = RecordingStore(tmp_path)
    assert is_safe_rec_id(bad_id) is False
    with pytest.raises(ValueError):
        store.save_audio(bad_id, b"x", SaveOpts("a.m4a", 0, 1000))


def test_upload_rejects_traversal_id(tmp_path: Path) -> None:
    client = TestClient(_server(tmp_path).app)
    resp = client.post(
        "/recording/upload",
        data={"id": "../../pwned", "started_ms": "0", "ended_ms": "1", "token": "tok"},
        files={"file": ("a.m4a", b"audio", "audio/mp4")},
    )
    assert resp.status_code == 400
    # Nothing was written outside the recordings root.
    assert not (tmp_path.parent / "pwned").exists()


def test_safe_rec_ids_accepted() -> None:
    for ok in ["rec-1718900000", "r1", "meeting_2026-06-24", "ABC.def"]:
        assert is_safe_rec_id(ok) is True
