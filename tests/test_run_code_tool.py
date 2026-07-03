"""run_code tool + code_sandbox facade: provider auto-selection, input
validation, output/error surfacing. The E2B/sandbox0 SDKs are mocked via the
facade so no token or network is needed."""

from __future__ import annotations

from pyclaudir import code_sandbox
from pyclaudir.tools.base import ToolContext
from pyclaudir.tools.run_code import RunCodeArgs, RunCodeTool


def _tool() -> RunCodeTool:
    return RunCodeTool(ToolContext())


# ── provider selection ───────────────────────────────────────────────────────


def test_provider_none_when_unconfigured(monkeypatch):
    for var in ("CODE_SANDBOX_PROVIDER", "E2B_API_KEY", "SANDBOX0_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert code_sandbox.provider() is None
    assert not code_sandbox.available()


def test_provider_autodetects_e2b(monkeypatch):
    monkeypatch.delenv("CODE_SANDBOX_PROVIDER", raising=False)
    monkeypatch.delenv("SANDBOX0_TOKEN", raising=False)
    monkeypatch.setenv("E2B_API_KEY", "e2b_xxx")
    assert code_sandbox.provider() == "e2b"


def test_explicit_provider_wins(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "e2b_xxx")
    monkeypatch.setenv("CODE_SANDBOX_PROVIDER", "sandbox0")
    assert code_sandbox.provider() == "sandbox0"


# ── tool behavior ────────────────────────────────────────────────────────────


async def test_refuses_non_owner_turn(monkeypatch):
    """The owner backstop: code never runs on a non-owner/webhook turn, even if
    the sandbox is configured and run_code slipped into the allowed set."""
    monkeypatch.setattr(code_sandbox, "available", lambda: True)
    ctx = ToolContext()
    ctx.owner_turn = False
    res = await RunCodeTool(ctx).run(RunCodeArgs(language="python", code="print(1)"))
    assert res.is_error
    assert "owner-only" in res.content


async def test_inert_without_token(monkeypatch):
    monkeypatch.setattr(code_sandbox, "available", lambda: False)
    res = await _tool().run(RunCodeArgs(language="python", code="print(1)"))
    assert res.is_error
    assert "E2B_API_KEY" in res.content


async def test_rejects_unsupported_language(monkeypatch):
    monkeypatch.setattr(code_sandbox, "available", lambda: True)
    res = await _tool().run(RunCodeArgs(language="malbolge", code="x"))
    assert res.is_error
    assert "Unsupported" in res.content


async def test_rejects_empty_code(monkeypatch):
    monkeypatch.setattr(code_sandbox, "available", lambda: True)
    res = await _tool().run(RunCodeArgs(language="python", code="   "))
    assert res.is_error


async def test_happy_path_returns_output(monkeypatch):
    monkeypatch.setattr(code_sandbox, "available", lambda: True)
    monkeypatch.setattr(
        code_sandbox,
        "run_code_sync",
        lambda lang, code: code_sandbox.SandboxResult(True, "42\n"),
    )
    res = await _tool().run(RunCodeArgs(language="python", code="print(6*7)"))
    assert not res.is_error
    assert "42" in res.content


async def test_sandbox_exception_is_tool_error(monkeypatch):
    monkeypatch.setattr(code_sandbox, "available", lambda: True)

    def boom(lang, code):
        raise RuntimeError("sandbox unreachable")

    monkeypatch.setattr(code_sandbox, "run_code_sync", boom)
    res = await _tool().run(RunCodeArgs(language="python", code="print(1)"))
    assert res.is_error
    assert "Sandbox error" in res.content


async def test_output_truncated(monkeypatch):
    monkeypatch.setattr(code_sandbox, "available", lambda: True)
    monkeypatch.setattr(
        code_sandbox,
        "run_code_sync",
        lambda lang, code: code_sandbox.SandboxResult(True, "A" * 20000),
    )
    res = await _tool().run(RunCodeArgs(language="python", code="x"))
    assert len(res.content) <= 8000


# ── defensive extractors ─────────────────────────────────────────────────────


def test_e2b_execution_extracts_stdout():
    class Logs:
        stdout = ["hello\n"]

    class Execution:
        error = None
        logs = Logs()
        text = None

    res = code_sandbox._from_e2b_execution(Execution())
    assert res.ok and "hello" in res.output


def test_e2b_execution_surfaces_error():
    class Err:
        name = "ValueError"
        value = "boom"

    class Execution:
        error = Err()

    res = code_sandbox._from_e2b_execution(Execution())
    assert not res.ok and "ValueError" in res.output


def test_rate_limit_blocks_runaway(monkeypatch):
    """A looping/poisoned task can't fan out unlimited sandboxes per minute."""
    monkeypatch.setenv("E2B_API_KEY", "e2b_xxx")
    monkeypatch.setattr(code_sandbox, "_MAX_PER_MIN", 3)
    monkeypatch.setattr(code_sandbox, "_recent_spawns", [])
    monkeypatch.setattr(
        code_sandbox,
        "_run_e2b",
        lambda lang, code: code_sandbox.SandboxResult(True, "ok"),
    )
    results = [code_sandbox.run_code_sync("python", "x") for _ in range(5)]
    assert sum(r.ok for r in results) == 3  # first 3 allowed
    assert any("too many" in r.output for r in results)  # rest blocked


def test_sandbox0_stringify_handles_shapes():
    assert code_sandbox._stringify(None) == "(no output)"

    class WithStdout:
        stdout = "hello"

    assert code_sandbox._stringify(WithStdout()) == "hello"
    assert code_sandbox._stringify("raw string") == "raw string"
