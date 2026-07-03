"""study_coach — SM-2 scheduling, quiz flow, nudge watcher throttling."""

from __future__ import annotations

import json

import pytest

import study_coach


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))


@pytest.mark.asyncio
async def test_add_and_quiz_flow():
    out = json.loads(
        await study_coach.dispatch(
            "add_flashcard", {"front": "你好", "back": "hello (nǐ hǎo)"}
        )
    )
    assert out["status"] == "card saved"
    quiz = json.loads(await study_coach.dispatch("quiz_me", {}))
    assert quiz["ask_him"] == "你好"
    assert quiz["correct_answer"].startswith("hello")
    graded = json.loads(
        await study_coach.dispatch(
            "grade_card", {"card_id": quiz["card_id"], "quality": 5}
        )
    )
    assert graded["status"] == "graded"
    # Answered perfectly → scheduled a day out, nothing due now.
    empty = json.loads(await study_coach.dispatch("quiz_me", {}))
    assert "nothing due" in empty["status"]


def test_sm2_failure_resets_success_grows():
    cid = study_coach.add_card("front", "back")
    assert study_coach.grade(cid, 5)  # rep 1 → 1 day
    assert study_coach.grade(cid, 5)  # rep 2 → 6 days
    assert study_coach.grade(cid, 5)  # rep 3 → 6 * ease
    con = study_coach._connect()
    interval, reps = con.execute(
        "SELECT interval_days, reps FROM study_items WHERE id = ?", (cid,)
    ).fetchone()
    con.close()
    assert reps == 3 and interval > 10
    study_coach.grade(cid, 1)  # blanked → reset
    con = study_coach._connect()
    interval, reps = con.execute(
        "SELECT interval_days, reps FROM study_items WHERE id = ?", (cid,)
    ).fetchone()
    con.close()
    assert reps == 0 and interval < 0.01


def test_grade_unknown_card():
    assert not study_coach.grade(999, 5)


def test_nudge_thresholds_and_throttle():
    assert study_coach.poll_study() == []  # nothing due
    for i in range(5):
        study_coach.add_card(f"f{i}", f"b{i}")
    events = study_coach.poll_study()
    assert len(events) == 1 and events[0].severity == "normal"
    study_coach.ack_study(events[0])
    assert study_coach.poll_study() == []  # throttled after the ack
