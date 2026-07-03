"""Tests for ClauseChunker.feed() and .flush()."""

from __future__ import annotations

from pyclaudir.cc_worker.chunker import ClauseChunker


# ── feed ──────────────────────────────────────────────────────────────────────


def test_feed_no_boundary_returns_empty() -> None:
    c = ClauseChunker()
    assert c.feed("hello world") == []


def test_feed_sentence_boundary_emits_chunk() -> None:
    c = ClauseChunker()
    chunks = c.feed("Hello world. ")
    assert len(chunks) == 1
    assert chunks[0] == "Hello world."


def test_feed_exclamation_mark_boundary() -> None:
    c = ClauseChunker()
    chunks = c.feed("Great job! ")
    assert chunks == ["Great job!"]


def test_feed_question_mark_boundary() -> None:
    c = ClauseChunker()
    chunks = c.feed("How are you? ")
    assert chunks == ["How are you?"]


def test_feed_comma_after_eight_nonspace_chars() -> None:
    c = ClauseChunker()
    # "abcdefgh," is exactly 8 non-space chars before the comma
    chunks = c.feed("abcdefgh, ")
    assert len(chunks) == 1


def test_feed_comma_too_short_no_boundary() -> None:
    c = ClauseChunker()
    # "abc," — only 3 non-space chars — should NOT split
    chunks = c.feed("abc, the rest")
    assert chunks == []


def test_feed_multiple_sentences_in_one_call() -> None:
    c = ClauseChunker()
    chunks = c.feed("Hello world. How are you? ")
    assert len(chunks) == 2
    assert "Hello world." in chunks
    assert "How are you?" in chunks


def test_feed_across_multiple_calls() -> None:
    c = ClauseChunker()
    assert c.feed("Hello") == []
    chunks = c.feed(" world. ")
    assert chunks == ["Hello world."]


def test_feed_skips_chunks_shorter_than_min() -> None:
    c = ClauseChunker()
    # "ok. " — 2 chars, below _MIN_CHUNK_CHARS=4 — should be filtered out
    chunks = c.feed("ok. next sentence here ")
    # "ok." is only 3 chars; should not appear
    assert all(len(ch) >= 4 for ch in chunks)


def test_feed_retains_tail_in_buffer() -> None:
    c = ClauseChunker()
    c.feed("Hello world. partial")
    # after split, "partial" stays in buffer
    chunks = c.flush()
    assert chunks == ["partial"]


# ── flush ─────────────────────────────────────────────────────────────────────


def test_flush_empty_buffer_returns_empty() -> None:
    c = ClauseChunker()
    assert c.flush() == []


def test_flush_returns_remaining_text() -> None:
    c = ClauseChunker()
    c.feed("no boundary here")
    result = c.flush()
    assert result == ["no boundary here"]


def test_flush_resets_buffer() -> None:
    c = ClauseChunker()
    c.feed("some text")
    c.flush()
    assert c.flush() == []


def test_flush_skips_very_short_tail() -> None:
    c = ClauseChunker()
    # feed something that leaves a 1-char tail
    c.feed("Hello world. x")
    result = c.flush()
    # "x" is 1 char — below MIN_CHUNK_CHARS
    assert result == []


def test_full_turn_workflow() -> None:
    c = ClauseChunker()
    chunks = []
    for word in ["The answer is", " forty-two.", " Trust me,", " I know things."]:
        chunks.extend(c.feed(word))
    chunks.extend(c.flush())
    joined = " ".join(chunks)
    assert "forty-two." in joined
    assert "Trust me," in joined or "I know things." in joined


def test_independent_instances_do_not_share_buffer() -> None:
    c1 = ClauseChunker()
    c2 = ClauseChunker()
    c1.feed("shared buffer would be bad")
    assert c2.flush() == []
