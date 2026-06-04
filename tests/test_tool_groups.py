from __future__ import annotations


from pyclaudir.tool_groups import build_allowed_tools, detect_extra_tools


def test_no_extras_for_plain_message() -> None:
    result = detect_extra_tools("hello how are you")
    assert result == frozenset()


def test_gmail_detected() -> None:
    result = detect_extra_tools("check my email inbox")
    assert "gmail_read" in result


def test_calendar_detected() -> None:
    result = detect_extra_tools("create a meeting for tomorrow")
    assert "calendar_create_event" in result


def test_multiple_modules() -> None:
    result = detect_extra_tools("send email with the slack message")
    assert "gmail_send" in result
    assert "slack_send" in result


def test_case_insensitive() -> None:
    result = detect_extra_tools("CHECK MY GMAIL")
    assert "gmail_read" in result
    assert len([t for t in result if t.startswith("gmail")]) > 0


def test_build_allowed_tools_combines() -> None:
    base = ("mcp__pyclaudir__now",)
    result = build_allowed_tools("check email", base)
    assert "mcp__pyclaudir__now" in result
    assert "gmail_read" in result


def test_build_allowed_tools_deduped() -> None:
    base = ("gmail_read", "gmail_send")
    result = build_allowed_tools("check my email inbox", base)
    assert len(result) == len(set(result))


def test_render_detected() -> None:
    result = detect_extra_tools("create a pdf of this")
    assert "mcp__pyclaudir__render_html" in result


def test_web_detected() -> None:
    result = detect_extra_tools("search online for news")
    assert "WebSearch" in result
