"""voice_intent.is_code_intent — high precision: fires on real run-code requests,
stays quiet on casual mentions (a false positive would delegate spuriously)."""

from __future__ import annotations

import pytest

from voice_intent import (
    is_code_intent,
    is_deactivate_intent,
    is_messages_intent,
    is_record_recall_intent,
    is_record_start_intent,
    is_record_stop_intent,
    is_search_intent,
    is_vision_intent,
)


@pytest.mark.parametrize(
    "text",
    [
        "write and run python to calculate the 20th fibonacci number",
        "run this code for me",
        "can you write a python script that sorts a list",
        "execute that",
        "debug this function please",
        "build a quick script to rename the files",
        "calculate the primes using python",
        "run the bash command to check disk space",
    ],
)
def test_fires_on_real_code_requests(text):
    assert is_code_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "remind me to call aziz at 5pm",
        "i wrote some code today, it was fun",
        "tell me a joke",
        "what's your favorite programming language",  # talks ABOUT code, no request
        "how are you doing",
        "",
        "ok",
    ],
)
def test_quiet_on_non_requests(text):
    assert is_code_intent(text) is False


@pytest.mark.parametrize(
    "text",
    [
        # fires ONLY on an explicit search command
        "search for the best laptops 2026",
        "look up who won the election",
        "look it up for me",
        "google the exchange rate for the dollar",
        "can you find out the score",
        "check online if that place is open",
        "do a web search on this",
    ],
)
def test_search_fires_on_explicit_request(text):
    assert is_search_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "check my messages",
        "any new dms?",
        "what's in my email",
        "anything new?",
        "catch me up",
        "any new telegram messages",
        "read me my notifications",
        "got any new messages",
    ],
)
def test_messages_fires_on_check(text):
    assert is_messages_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # sending a message must NOT trigger a buffer dump (precision guard)
        "message aziz that i'm running late",
        "did my message send",
        "leave him a message",
        "send a text to mom",
        "tell aziz i'll be there",
        # plain chitchat
        "how are you doing",
        "tell me a joke",
        "",
    ],
)
def test_messages_quiet_on_send_or_chitchat(text):
    assert is_messages_intent(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "what is this",
        "what's this thing",
        "what am i looking at",
        "look at this",
        "read this for me",
        "translate this menu",
        "who is this in front of me",
        "what do you see",
        "can you see this",
    ],
)
def test_vision_fires_on_look_requests(text):
    assert is_vision_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # search verbs must NOT trigger the camera (the collision the review flagged)
        "look it up",
        "look up the score",
        "search for the weather",
        "google it",
        "find out who won",
        # chitchat
        "how are you",
        "tell me a joke",
        "",
    ],
)
def test_vision_quiet_on_search_or_chitchat(text):
    assert is_vision_intent(text) is False


def test_vision_and_search_are_mutually_exclusive():
    # the two functions must never both fire on the same utterance
    for phrase in ["look at this", "look it up", "what is this", "search for x"]:
        assert not (is_vision_intent(phrase) and is_search_intent(phrase))


@pytest.mark.parametrize(
    "text",
    [
        "shut up",
        "shutup",
        "shut the fuck up",  # real transcript — filler between shut/up (prod bug)
        "shut the hell up",
        "be quiet",
        "stop listening",
        "go to sleep",
        "sleep mode",
        "deactivate",
        "deactivation",  # real transcript — STT gave the noun, not the verb
        "act, deactivation",
        "turn yourself off",
        "leave me alone",
        "okay that's all nemo",
        "goodbye nemo",
        "nemo shut up",
    ],
)
def test_deactivate_fires_on_off_commands(text):
    assert is_deactivate_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # must NOT deactivate on normal requests
        "stop the timer",
        "set a reminder",
        "what's the weather",
        "stop the music",  # not 'stop listening/talking'
        "how are you",
        "",
    ],
)
def test_deactivate_quiet_on_normal_requests(text):
    assert is_deactivate_intent(text) is False


@pytest.mark.parametrize(
    "text",
    [
        # NOT explicit → Nemo answers from his own knowledge, no surprise search
        "what's the score between spain and saudi arabia right now",
        "what's the weather in tashkent today",
        "what's the latest news on the match",
        "how much is a flight to singapore",
        "who won the election",
        "remind me to call aziz",
        "tell me a joke",
        "",
    ],
)
def test_search_quiet_unless_asked(text):
    assert is_search_intent(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "record this",
        "record this conversation",
        "start recording",
        "record the meeting",
        "record our call",
        "begin recording now",
        "record everything",
        "can you record this",
    ],
)
def test_record_start_fires(text):
    assert is_record_start_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "stop recording",
        "stop the recording",
        "end recording",
        "finish recording",
        "okay we're done with the recording",
        "wrap up the recording",
    ],
)
def test_record_stop_fires(text):
    assert is_record_stop_intent(text) is True
    # stop must NOT also read as start
    assert is_record_start_intent(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "summarize what we talked about",
        "give me a recap of the meeting",
        "send me the transcript",
        "what did we discuss in the recording",
        "read me the transcript",
        "summarize the recording",
        "what were we talking about earlier",
    ],
)
def test_record_recall_fires(text):
    assert is_record_recall_intent(text) is True
    # recall must NOT read as a fresh start
    assert is_record_start_intent(text) is False


@pytest.mark.parametrize(
    "text",
    [
        # must NOT start/stop a recording
        "stop the timer",
        "stop the music",
        "what's the weather",
        "set a reminder",
        "how are you",
        "play some music",
        "",
    ],
)
def test_record_quiet_on_unrelated(text):
    assert is_record_start_intent(text) is False
    assert is_record_stop_intent(text) is False
