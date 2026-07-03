"""Characterization tests for the capability registry refactor.

Pins the composed prompt/tool surface to what the monolithic voice_brain
produced, so the registry stays a pure relocation.
"""

from __future__ import annotations

import json

import pytest

import assistant_tools
import capabilities
import memory_tools
import messages
import phone_tools
import reminders
import skills
import vision
import voice_brain
import web_search


def test_identity_prose_order() -> None:
    identity = voice_brain._IDENTITY
    markers = [
        "You are Nemo",  # persona first
        "You can control his phone",
        "YOU CAN SEE",
        "RECORDING MEETINGS",
        "PRIVACY — read only when asked",
        "IMPORTANT: phone control needs the Nemo Accessibility Service",
        "How your hands work",
        "BE FAST and never leave him in silence",
        "You share Avazbek's memory",
        "When he says to remind him of something",
    ]
    positions = [identity.index(m) for m in markers]
    assert positions == sorted(positions)


def test_functions_cover_every_module() -> None:
    names = {f["name"] for f in voice_brain.FUNCTIONS}
    expected = (
        memory_tools.TOOL_NAMES
        | phone_tools.PHONE_TOOL_NAMES
        | reminders.TOOL_NAMES
        | skills.TOOL_NAMES
        | vision.TOOL_NAMES
        | web_search.TOOL_NAMES
        | assistant_tools.TOOL_NAMES
    )
    assert names == expected


def test_messages_dispatch_only() -> None:
    exposed = {f["name"] for f in capabilities.all_functions()}
    assert messages.TOOL_NAMES.isdisjoint(exposed)
    owner = [c for c in capabilities.REGISTRY if c.id == "messages"]
    assert owner and owner[0].dispatch is not None


def test_tool_names_disjoint_except_web_search_shadow() -> None:
    # Known, intentional overlap: the CLI skill `web_search` shadows the
    # web_search module tool (skills capability registered first).
    seen: set[str] = set()
    for cap in capabilities.REGISTRY:
        overlap = cap.tool_names & seen
        assert overlap <= {"web_search"}, cap.id
        seen |= cap.tool_names


@pytest.mark.asyncio
async def test_web_search_routes_to_cli_skill(monkeypatch) -> None:
    monkeypatch.setattr(
        skills, "dispatch", lambda name, args: json.dumps({"via": "skills"})
    )
    monkeypatch.setattr(
        web_search, "dispatch", lambda name, args: json.dumps({"via": "module"})
    )
    out = json.loads(await voice_brain.dispatch("web_search", {"query": "x"}))
    assert out == {"via": "skills"}


@pytest.mark.asyncio
async def test_dispatch_late_binds_module_functions(monkeypatch) -> None:
    monkeypatch.setattr(
        assistant_tools, "dispatch", lambda name, args: json.dumps({"hit": name})
    )
    out = json.loads(await voice_brain.dispatch("calculate", {"expression": "1"}))
    assert out == {"hit": "calculate"}


@pytest.mark.asyncio
async def test_dispatch_unknown_tool() -> None:
    out = json.loads(await voice_brain.dispatch("no_such_tool", {}))
    assert out == {"error": "unknown function no_such_tool"}


@pytest.mark.asyncio
async def test_dispatch_never_raises(monkeypatch) -> None:
    def boom(name: str, args: dict) -> str:
        raise RuntimeError("kaput")

    monkeypatch.setattr(skills, "dispatch", boom)
    name = next(iter(skills.TOOL_NAMES))
    out = json.loads(await voice_brain.dispatch(name, {}))
    assert out == {"error": "kaput"}


def test_tier2_matches_uses_registry_patterns(monkeypatch) -> None:
    assert capabilities.tier2_matches("what's the weather") is False
    cap = capabilities.Capability(id="heavy", tier2_patterns=(r"\btranscribe\b",))
    monkeypatch.setattr(capabilities, "REGISTRY", (*capabilities.REGISTRY, cap))
    assert capabilities.tier2_matches("please Transcribe the meeting") is True


def test_route_tier_consults_registry(monkeypatch) -> None:
    from semantic_router import RouteDecision, route_tier

    cap = capabilities.Capability(id="heavy", tier2_patterns=(r"\bdeep dive\b",))
    monkeypatch.setattr(capabilities, "REGISTRY", (*capabilities.REGISTRY, cap))
    assert route_tier("do a deep dive on this") == RouteDecision.TIER2_ENGINE
    assert route_tier("hey how are you") == RouteDecision.TIER0_VOICE


@pytest.mark.asyncio
async def test_context_for_gathers_fetchers(monkeypatch) -> None:
    async def fake_fetch(query: str) -> list[str]:
        return [f"mem:{query}"]

    cap = capabilities.Capability(id="ctx", context_fetcher=fake_fetch)
    monkeypatch.setattr(capabilities, "REGISTRY", (cap,))
    assert await capabilities.context_for("coffee") == ["mem:coffee"]


@pytest.mark.asyncio
async def test_context_for_swallows_fetcher_errors(monkeypatch) -> None:
    async def bad_fetch(query: str) -> list[str]:
        raise RuntimeError("db gone")

    async def good_fetch(query: str) -> list[str]:
        return ["ok"]

    caps = (
        capabilities.Capability(id="bad", context_fetcher=bad_fetch),
        capabilities.Capability(id="good", context_fetcher=good_fetch),
    )
    monkeypatch.setattr(capabilities, "REGISTRY", caps)
    assert await capabilities.context_for("x") == ["ok"]
