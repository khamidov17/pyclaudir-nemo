"""interruption_log — engagement windows, demotion threshold, content-free."""

from __future__ import annotations

import time

import pytest

import interruption_log
import memory_store


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))


def _backdate(rid: int, seconds: float) -> None:
    con = memory_store.connect()
    con.execute(
        "UPDATE interruptions SET ts = ? WHERE id = ?", (time.time() - seconds, rid)
    )
    con.commit()
    con.close()


def test_speech_within_window_engages():
    rid = interruption_log.record_delivery("followup", "speak")
    interruption_log.on_user_speech()
    con = memory_store.connect()
    (engaged,) = con.execute(
        "SELECT engaged FROM interruptions WHERE id = ?", (rid,)
    ).fetchone()
    con.close()
    assert engaged == 1


def test_old_delivery_marked_ignored():
    rid = interruption_log.record_delivery("followup", "speak")
    _backdate(rid, 300)
    interruption_log.on_user_speech()
    con = memory_store.connect()
    (engaged,) = con.execute(
        "SELECT engaged FROM interruptions WHERE id = ?", (rid,)
    ).fetchone()
    con.close()
    assert engaged == 0


def test_demotion_after_repeated_ignoring():
    assert not interruption_log.demoted("infra")  # no history yet
    for _ in range(5):
        rid = interruption_log.record_delivery("infra", "speak")
        _backdate(rid, 300)
    interruption_log.on_user_speech()
    assert interruption_log.demoted("infra")
    assert not interruption_log.demoted("followup")  # per-source


def test_engaged_source_not_demoted():
    for _ in range(5):
        interruption_log.record_delivery("followup", "speak")
        interruption_log.on_user_speech()
    assert not interruption_log.demoted("followup")


def test_no_content_stored():
    interruption_log.record_delivery("followup", "speak")
    con = memory_store.connect()
    cols = [c[1] for c in con.execute("PRAGMA table_info(interruptions)")]
    con.close()
    assert "text" not in cols and "content" not in cols
