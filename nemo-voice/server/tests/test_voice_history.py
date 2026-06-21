"""voice_history.recent_items — the freshness gate for seeding a reconnected
session (continue a recent chat, but never 'continue' a stale one)."""

from __future__ import annotations

import time

import voice_history


def _seed(monkeypatch, items):
    monkeypatch.setattr(voice_history, "_load_recent", lambda: items)


def test_recent_items_returns_fresh_turns(monkeypatch):
    now = time.time()
    _seed(
        monkeypatch,
        [
            {"role": "user", "text": "what's the weather", "ts": now - 30},
            {"role": "nemo", "text": "sunny and warm", "ts": now - 25},
        ],
    )
    items = voice_history.recent_items()
    assert [i["role"] for i in items] == ["user", "nemo"]
    assert items[0]["text"] == "what's the weather"
    # only role+text exposed for seeding (no ts leak)
    assert set(items[0]) == {"role", "text"}


def test_stale_history_is_not_resumed(monkeypatch):
    old = time.time() - 3600  # an hour ago = a different conversation
    _seed(monkeypatch, [{"role": "user", "text": "old stuff", "ts": old}])
    assert voice_history.recent_items(max_age_sec=900) == []


def test_drops_individual_stale_turns(monkeypatch):
    now = time.time()
    _seed(
        monkeypatch,
        [
            {"role": "user", "text": "way earlier", "ts": now - 5000},
            {"role": "user", "text": "just now", "ts": now - 10},
        ],
    )
    items = voice_history.recent_items(max_age_sec=900)
    assert [i["text"] for i in items] == ["just now"]


def test_empty_history(monkeypatch):
    _seed(monkeypatch, [])
    assert voice_history.recent_items() == []


def test_missing_ts_treated_as_stale(monkeypatch):
    # legacy rows without a ts must not be resurrected as a live conversation
    _seed(monkeypatch, [{"role": "user", "text": "legacy"}])
    assert voice_history.recent_items() == []
