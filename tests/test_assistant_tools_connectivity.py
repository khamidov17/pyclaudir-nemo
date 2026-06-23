"""Two things the assistant tools must get right beyond "the math is correct":

1. calculate must not be a DoS vector — nested exponentiation can't blow up the
   (non-sandboxed) engine process.
2. The tools must actually be REACHABLE for natural phrasings, not only when the
   user happens to say a trigger word like "calculate"/"convert".
"""

from __future__ import annotations

import pytest

from pyclaudir.tools.calculate import CalculateError, safe_eval
from pyclaudir.tool_groups import detect_extra_tools

_ASSISTANT = {"calculate", "convert_units", "world_time"}


def _reaches(text: str) -> set[str]:
    return {t.split("__")[-1] for t in detect_extra_tools(text)} & _ASSISTANT


# ── DoS guard ────────────────────────────────────────────────────────────────


def test_normal_math_still_works() -> None:
    assert safe_eval("2 + 2 * 3") == 8
    assert safe_eval("sqrt(144)") == 12
    assert isinstance(safe_eval("2 ** 1000"), int)  # a single big power is fine


@pytest.mark.parametrize(
    "expr",
    ["(9 ** 1000) ** 1000", "((2 ** 1000) ** 1000) ** 1000", "10 ** 999 ** 2"],
)
def test_nested_exponent_blowup_blocked(expr: str) -> None:
    with pytest.raises(CalculateError):
        safe_eval(expr)


# ── Reachability ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "what's 15% of 80",
        "how many km in 5 miles",
        "square root of 144",
        "5 miles in km",
        "tip on $60",
        "convert 10 lbs to kg",
        "what time is it in tokyo",
        "how much is 12 times 7",
    ],
)
def test_natural_requests_offer_the_tools(text: str) -> None:
    assert _reaches(text), f"no assistant tool offered for {text!r}"
