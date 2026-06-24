"""CalculateTool — safe arithmetic evaluation, edge cases, and security.

The security half is the important part: the evaluator must reject names,
attribute access, ``__import__``, and any call to a function outside the
small whitelist. It uses ``ast`` (not ``eval``), so these can never execute.
"""

from __future__ import annotations

import pytest

from pyclaudir.tools.base import ToolContext
from pyclaudir.tools.calculate import (
    CalculateArgs,
    CalculateError,
    CalculateTool,
    safe_eval,
)


def test_long_flat_chain_does_not_overflow() -> None:
    # A flat operator chain recurses once per BinOp; without the length cap this
    # raised an uncaught RecursionError and wedged the turn. Now a clean error.
    expr = "1" + "+1" * 5000
    with pytest.raises(CalculateError):
        safe_eval(expr)


def test_overlong_expression_rejected() -> None:
    with pytest.raises(CalculateError, match="too long"):
        safe_eval("1+" * 600 + "1")  # > 1000 chars


@pytest.mark.asyncio
async def test_tool_run_long_chain_clean_error() -> None:
    tool = CalculateTool(ToolContext())
    result = await tool.run(CalculateArgs(expression="1" + "+1" * 5000))
    assert result.is_error
    assert "error:" in result.content


# ---------------------------------------------------------------------------
# Happy paths (pure function)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("1 + 2", 3),
        ("2 * 3 + 4", 10),
        ("(2 + 3) * 4", 20),
        ("10 / 4", 2.5),
        ("10 // 3", 3),
        ("10 % 3", 1),
        ("2 ** 8", 256),
        ("-5 + 3", -2),
        ("+7", 7),
        ("--3", 3),
        ("sqrt(16)", 4.0),
        ("abs(-12)", 12),
        ("round(3.14159, 2)", 3.14),
        ("min(4, 2, 9)", 2),
        ("max(4, 2, 9)", 9),
        ("sqrt(2) ** 2", pytest.approx(2.0)),
        ("3.5 * 2", 7.0),
    ],
)
def test_safe_eval_happy(expr: str, expected) -> None:
    assert safe_eval(expr) == expected


def test_nested_functions() -> None:
    assert safe_eval("max(min(10, 3), sqrt(4))") == 3


# ---------------------------------------------------------------------------
# Edge cases / arithmetic errors
# ---------------------------------------------------------------------------


def test_division_by_zero() -> None:
    with pytest.raises(CalculateError, match="division by zero"):
        safe_eval("1 / 0")


def test_empty_expression() -> None:
    with pytest.raises(CalculateError, match="empty"):
        safe_eval("   ")


def test_syntax_error() -> None:
    with pytest.raises(CalculateError, match="syntax error"):
        safe_eval("2 +")


def test_huge_exponent_rejected() -> None:
    with pytest.raises(CalculateError, match="exponent too large"):
        safe_eval("2 ** 10000000")


# ---------------------------------------------------------------------------
# SECURITY — every one of these must be rejected, never executed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "__import__('os').system('echo pwned')",
        "os.system('ls')",
        "().__class__",
        "x",  # bare name
        "pi",  # not a whitelisted name
        "open('/etc/passwd')",
        "eval('1+1')",
        "exec('x=1')",
        "len([1,2,3])",
        "[i for i in range(3)]",
        "{1: 2}",
        "lambda: 1",
        "(1).__class__.__bases__",
        "math.sqrt(4)",  # attribute access not allowed
        "sqrt.__call__(4)",
        "True",  # booleans are not numbers
        "1 if True else 2",
        "'a' * 3",  # string literal
    ],
)
def test_security_rejections(hostile: str) -> None:
    with pytest.raises(CalculateError):
        safe_eval(hostile)


# ---------------------------------------------------------------------------
# Tool wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_run_success() -> None:
    tool = CalculateTool(ToolContext())
    result = await tool.run(CalculateArgs(expression="2 + 2 * 3"))
    assert not result.is_error
    assert result.content == "8"
    assert result.data == {"expression": "2 + 2 * 3", "result": 8}


@pytest.mark.asyncio
async def test_tool_run_rejects_hostile() -> None:
    tool = CalculateTool(ToolContext())
    result = await tool.run(CalculateArgs(expression="__import__('os')"))
    assert result.is_error
    assert "error:" in result.content


@pytest.mark.asyncio
async def test_tool_run_division_by_zero() -> None:
    tool = CalculateTool(ToolContext())
    result = await tool.run(CalculateArgs(expression="5 / 0"))
    assert result.is_error
    assert "division by zero" in result.content
