"""Tests for voice_http helper functions."""

from __future__ import annotations

import hashlib
import hmac
import os
import time

import pytest

# ── module-level import with env var preset ───────────────────────────────────
_TOKEN = "test-secret-token"
os.environ.setdefault("VOICE_INTERNAL_TOKEN", _TOKEN)

import voice_http  # noqa: E402


# ── _verify_internal ──────────────────────────────────────────────────────────


def _make_sig(body: bytes, token: str = _TOKEN) -> str:
    return hmac.new(token.encode(), body, hashlib.sha256).hexdigest()


def test_verify_internal_valid_sig() -> None:
    body = b'{"session_id":"abc"}'
    sig = _make_sig(body)
    # patch token in module for the test
    orig = voice_http._INTERNAL_TOKEN
    voice_http._INTERNAL_TOKEN = _TOKEN
    assert voice_http._verify_internal(body, sig) is True
    voice_http._INTERNAL_TOKEN = orig


def test_verify_internal_wrong_sig() -> None:
    body = b'{"session_id":"abc"}'
    voice_http._INTERNAL_TOKEN = _TOKEN
    assert voice_http._verify_internal(body, "badhex") is False


def test_verify_internal_empty_token_returns_false() -> None:
    orig = voice_http._INTERNAL_TOKEN
    voice_http._INTERNAL_TOKEN = ""
    body = b"anything"
    sig = _make_sig(body)
    assert voice_http._verify_internal(body, sig) is False
    voice_http._INTERNAL_TOKEN = orig


# ── _sanitize_chunk ───────────────────────────────────────────────────────────


def test_sanitize_chunk_strips_xml_tags() -> None:
    result = voice_http._sanitize_chunk("<tool>inject</tool> hello")
    assert "<tool>" not in result
    assert "hello" in result


def test_sanitize_chunk_caps_at_2000() -> None:
    long_text = "a" * 3000
    result = voice_http._sanitize_chunk(long_text)
    assert len(result) == 2000


def test_sanitize_chunk_short_text_unchanged() -> None:
    assert voice_http._sanitize_chunk("hello world") == "hello world"


def test_sanitize_chunk_empty_string() -> None:
    assert voice_http._sanitize_chunk("") == ""


# ── _rate_ok ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_rate() -> None:
    voice_http._RATE.clear()
    yield
    voice_http._RATE.clear()


def test_rate_ok_allows_first_request() -> None:
    assert voice_http._rate_ok("sess-aaa") is True


def test_rate_ok_blocks_after_limit() -> None:
    sid = "sess-bbb"
    # fill bucket to limit
    now = time.monotonic()
    voice_http._RATE[sid] = [now] * voice_http._RATE_MAX
    assert voice_http._rate_ok(sid) is False


def test_rate_ok_allows_after_window_expires() -> None:
    sid = "sess-ccc"
    old_time = time.monotonic() - voice_http._RATE_WINDOW - 1
    voice_http._RATE[sid] = [old_time] * voice_http._RATE_MAX
    assert voice_http._rate_ok(sid) is True


def test_rate_ok_independent_sessions() -> None:
    now = time.monotonic()
    voice_http._RATE["saturated"] = [now] * voice_http._RATE_MAX
    assert voice_http._rate_ok("fresh-session") is True
