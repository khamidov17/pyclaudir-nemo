"""voice_intent.is_code_intent — high precision: fires on real run-code requests,
stays quiet on casual mentions (a false positive would delegate spuriously)."""

from __future__ import annotations

import pytest

from voice_intent import is_code_intent, is_messages_intent, is_search_intent


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
