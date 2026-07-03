"""``calculate`` — safe arithmetic expression evaluator.

No ``eval``. The expression is parsed with the stdlib :mod:`ast` module and
walked against a strict whitelist of node types, operators, and a handful of
math functions. Anything outside the whitelist — names, attribute access,
arbitrary calls, comprehensions, ``__import__`` and friends — is rejected
before any evaluation happens.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any, Callable

from pydantic import BaseModel, Field

from .base import BaseTool, ToolResult


class CalculateError(ValueError):
    """Raised when an expression is unsafe or cannot be evaluated."""


# Whitelisted binary operators.
_BIN_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# Whitelisted unary operators.
_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Whitelisted callable functions. Each entry is a plain Python callable that
# operates only on numbers — no I/O, no attribute access, no surprises.
_FUNCS: dict[str, Callable[..., Any]] = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
}

# Guard rail: exponent magnitude cap so ``2 ** 10_000_000`` can't be used to
# burn CPU / memory. Picked high enough for any legitimate query.
_MAX_POW_EXPONENT = 1000

# A single exponent cap is NOT enough: nested powers like ``(9**1000)**1000``
# each pass the exponent check yet produce an astronomically large integer that
# blocks the (non-sandboxed) engine process. So also bound the SIZE of every
# intermediate integer result. 4096 bits (~1233 digits) is far beyond any real
# calculator need and well short of a memory problem.
_MAX_RESULT_BITS = 4096

# Bound the raw expression length. _eval_node recurses once per operator, so a
# long flat chain ("1+1+1+…") would overflow the stack before any size guard
# runs. 1000 chars is far more than any real spoken/typed calculation.
_MAX_EXPR_LEN = 1000


def _guard_size(value: Any) -> Any:
    """Reject integer results large enough to threaten CPU/memory."""
    if isinstance(value, int) and value.bit_length() > _MAX_RESULT_BITS:
        raise CalculateError("result too large")
    return value


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    # Numeric literals only. (ast.Num is deprecated; Constant covers it.)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculateError(
                f"only numeric literals are allowed, got {node.value!r}"
            )
        return node.value

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        fn = _BIN_OPS.get(op_type)
        if fn is None:
            raise CalculateError(f"operator {op_type.__name__} is not allowed")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if op_type is ast.Pow and isinstance(right, (int, float)):
            if abs(right) > _MAX_POW_EXPONENT:
                raise CalculateError("exponent too large")
        return _guard_size(fn(left, right))

    if isinstance(node, ast.UnaryOp):
        u_op_type = type(node.op)
        u_fn = _UNARY_OPS.get(u_op_type)
        if u_fn is None:
            raise CalculateError(f"unary operator {u_op_type.__name__} is not allowed")
        return u_fn(_eval_node(node.operand))

    if isinstance(node, ast.Call):
        # Only bare-name calls to whitelisted functions. No attribute access
        # (``math.system``), no keywords, no *args/**kwargs.
        if not isinstance(node.func, ast.Name):
            raise CalculateError(
                "only direct calls to whitelisted functions are allowed"
            )
        name = node.func.id
        fn = _FUNCS.get(name)
        if fn is None:
            raise CalculateError(f"function {name!r} is not allowed")
        if node.keywords:
            raise CalculateError("keyword arguments are not allowed")
        args = [_eval_node(a) for a in node.args]
        return fn(*args)

    raise CalculateError(f"expression element {type(node).__name__} is not allowed")


def safe_eval(expression: str) -> float | int:
    """Parse and evaluate ``expression`` against the whitelist.

    Raises :class:`CalculateError` for anything unsafe or malformed, and for
    runtime arithmetic failures (e.g. division by zero).
    """
    if not isinstance(expression, str) or not expression.strip():
        raise CalculateError("empty expression")
    # Bound input length: a flat operator chain ("1+1+1+…") recurses once per
    # BinOp in _eval_node, so a long expression overflows the stack. Capping the
    # length bounds both the parse cost and the recursion depth.
    if len(expression) > _MAX_EXPR_LEN:
        raise CalculateError("expression too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculateError(f"syntax error: {exc.msg}") from exc
    try:
        result = _eval_node(tree)
    except CalculateError:
        raise
    except ZeroDivisionError as exc:
        raise CalculateError("division by zero") from exc
    except RecursionError as exc:
        raise CalculateError("expression too complex") from exc
    except (ValueError, OverflowError, TypeError) as exc:
        raise CalculateError(str(exc)) from exc
    if not isinstance(result, (int, float)) or isinstance(result, bool):
        raise CalculateError("expression did not evaluate to a number")
    return result


class CalculateArgs(BaseModel):
    expression: str = Field(
        description=(
            "An arithmetic expression. Supports + - * / // % ** parentheses, "
            "unary minus, and the functions sqrt, abs, round, min, max. "
            "No variables, attribute access, or other function calls."
        )
    )


class CalculateTool(BaseTool):
    name = "calculate"
    description = (
        "Safely evaluate an arithmetic expression (offline, no network). "
        "Supports + - * / // % ** parentheses, unary minus, and the functions "
        "sqrt, abs, round, min, max. Rejects anything else — variables, "
        "attribute access, arbitrary function calls, imports."
    )
    args_model = CalculateArgs

    async def run(self, args: CalculateArgs) -> ToolResult:
        try:
            value = safe_eval(args.expression)
        except CalculateError as exc:
            return ToolResult(content=f"error: {exc}", is_error=True)
        return ToolResult(
            content=str(value),
            data={"expression": args.expression, "result": value},
        )
