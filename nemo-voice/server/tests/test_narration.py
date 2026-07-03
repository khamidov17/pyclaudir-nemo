"""narration — toggle intents, throttle, dedupe, 'clear' suppression."""

from __future__ import annotations

import pytest

import narration
import vision


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    narration.reset()
    monkeypatch.setattr(narration, "_MIN_INTERVAL_S", 0.0)  # no time-throttle in tests


def test_toggle_intents():
    assert narration.on_intent("describe my surroundings")
    assert narration.on_intent("scene mode please")
    assert narration.on_intent("atrofni ayt")
    assert narration.is_off_intent("stop describing")
    assert not narration.on_intent("this scene is nice")


def test_describe_and_dedupe(monkeypatch):
    seq = iter(["A door on your left.", "A door on your left.", "Steps ahead."])
    monkeypatch.setattr(vision, "describe", lambda *a, **k: next(seq))
    assert narration.describe_frame("img") == "A door on your left."
    assert narration.describe_frame("img") is None  # same scene → silent
    assert narration.describe_frame("img") == "Steps ahead."


def test_clear_scene_is_silent(monkeypatch):
    monkeypatch.setattr(vision, "describe", lambda *a, **k: "clear")
    assert narration.describe_frame("img") is None


def test_time_throttle(monkeypatch):
    monkeypatch.setattr(narration, "_MIN_INTERVAL_S", 999.0)
    monkeypatch.setattr(vision, "describe", lambda *a, **k: "Something.")
    assert narration.describe_frame("img") == "Something."
    assert narration.describe_frame("img") is None  # within throttle window


def test_vl_failure_silent(monkeypatch):
    monkeypatch.setattr(vision, "describe", lambda *a, **k: None)
    assert narration.describe_frame("img") is None
