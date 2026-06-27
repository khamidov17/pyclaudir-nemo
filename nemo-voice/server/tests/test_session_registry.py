"""Tests for session_registry module."""

from __future__ import annotations

import pytest

import session_registry


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """Reset module-level registry state between tests."""
    session_registry._registry.clear()
    yield
    session_registry._registry.clear()


def test_register_and_get() -> None:
    obj = object()
    session_registry.register("sess-1", obj)
    assert session_registry.get("sess-1") is obj


def test_get_missing_returns_none() -> None:
    assert session_registry.get("nonexistent") is None


def test_unregister_removes_entry() -> None:
    obj = object()
    session_registry.register("sess-2", obj)
    session_registry.unregister("sess-2")
    assert session_registry.get("sess-2") is None


def test_unregister_missing_is_noop() -> None:
    session_registry.unregister("does-not-exist")  # must not raise


def test_active_count_empty() -> None:
    assert session_registry.active_count() == 0


def test_active_count_tracks_registrations() -> None:
    session_registry.register("a", object())
    session_registry.register("b", object())
    assert session_registry.active_count() == 2


def test_active_count_after_unregister() -> None:
    session_registry.register("a", object())
    session_registry.register("b", object())
    session_registry.unregister("a")
    assert session_registry.active_count() == 1


def test_register_overwrites_existing_session() -> None:
    obj1 = object()
    obj2 = object()
    session_registry.register("dup", obj1)
    session_registry.register("dup", obj2)
    assert session_registry.get("dup") is obj2
    assert session_registry.active_count() == 1


def test_multiple_independent_sessions() -> None:
    o1, o2, o3 = object(), object(), object()
    session_registry.register("x", o1)
    session_registry.register("y", o2)
    session_registry.register("z", o3)
    assert session_registry.get("x") is o1
    assert session_registry.get("y") is o2
    assert session_registry.get("z") is o3
    assert session_registry.active_count() == 3


def test_any_active_returns_none_when_empty() -> None:
    assert session_registry.any_active() is None


def test_any_active_returns_last_registered() -> None:
    o1, o2 = object(), object()
    session_registry.register("first", o1)
    session_registry.register("last", o2)
    assert session_registry.any_active() is o2


def test_any_active_returns_none_after_unregister_all() -> None:
    o = object()
    session_registry.register("s", o)
    session_registry.unregister("s")
    assert session_registry.any_active() is None
