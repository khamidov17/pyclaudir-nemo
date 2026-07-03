"""Deterministic code-execution intent — a safety net for the realtime model.

Qwen sometimes answers a "run this code" request in its head instead of calling
`delegate_task`, so nothing actually runs (the user just hears nothing useful).
We can't fully trust the model's tool discipline, so we detect a clear code/run
request from the user's OWN words and, if the model finishes the turn WITHOUT
delegating, recover by delegating it ourselves. Recovering only on the miss (not
pre-emptively) means a code task can never run twice.

High precision on purpose: casual mentions ("I wrote some code today") must not
trigger a delegation — only an actual request to run/write/execute code.
"""

from __future__ import annotations

import re

_CODE = r"(code|script|program|function|algorithm|python|javascript|node\.?js|bash|sql)"
_PATTERNS = [
    # "run/write/build/debug ... <code-word>"
    re.compile(
        rf"\b(run|execute|write|build|debug|fix|create|make)\b.{{0,30}}\b{_CODE}\b",
        re.I,
    ),
    # "<code-word> ... run/execute/script"
    re.compile(rf"\b{_CODE}\b.{{0,30}}\b(run|execute|script|program)\b", re.I),
    # "run/execute this|that|it"
    re.compile(r"\b(run|execute)\s+(this|that|it|the)\b", re.I),
    # "calculate ... with/using/in code|python"
    re.compile(
        r"\bcalculate\b.{0,40}\b(with|using|in)\s+(code|python|a script)\b", re.I
    ),
]


def is_code_intent(text: str) -> bool:
    """True when the user clearly asked to run/write/execute code."""
    t = (text or "").strip()
    if len(t) < 4:
        return False
    return any(p.search(t) for p in _PATTERNS)


# By design, fire ONLY on an EXPLICIT request to search. Otherwise Nemo answers
# from his own knowledge — Avazbek asks to search when he actually needs current
# info, so we never guess (no surprise searches on "what's the weather").
_SEARCH_PATTERNS = [
    re.compile(
        r"\b(search|google(\s+it)?|look\s+(up|it\s+up|that\s+up)|"
        r"find\s+(out|online|me)|web\s*search|"
        r"check\s+(online|the\s+web|the\s+internet))\b",
        re.I,
    ),
]


def is_search_intent(text: str) -> bool:
    """True only when the user EXPLICITLY asked to search the web."""
    t = (text or "").strip()
    if len(t) < 4:
        return False
    return any(p.search(t) for p in _SEARCH_PATTERNS)


# "Check my messages" intent. High precision — must NOT collide with sending a
# message ("message Aziz", "did my message send", "leave him a message"). Keys on
# CHECK/ANY-NEW + messages/DMs/notifications/inbox/email, never on "message X".
_MESSAGES_PATTERNS = [
    re.compile(
        r"\b(check|read|any|got|new|show|catch me up on|what'?s in)\b[^.?!]*?\b"
        r"(messages|message|dms?|texts?|notifications?|inbox|emails?|mail|telegram|whatsapp)\b",
        re.I,
    ),
    re.compile(r"\b(any\s+new|anything\s+new|what'?s\s+new)\b", re.I),
    re.compile(r"\bcatch me up\b", re.I),
]
# Block sending phrases that would otherwise match (precision guard).
_MESSAGES_BLOCK = re.compile(
    r"\b(message|text|dm|email|write|send|tell|leave)\s+(him|her|them|to\s+|"
    r"[A-Z][a-z]+)|did\s+my\s+message|message\s+(send|sent|go|went)",
    re.I,
)


def is_messages_intent(text: str) -> bool:
    """True only when the user asked to CHECK their incoming messages — not to
    send one."""
    t = (text or "").strip()
    if len(t) < 4 or _MESSAGES_BLOCK.search(t):
        return False
    return any(p.search(t) for p in _MESSAGES_PATTERNS)


# "Look at what the camera sees" intent. Must NOT collide with web search:
# "look UP / look it up" = search; "look AT this" = vision.
_VISION_PATTERNS = [
    re.compile(r"\b(what'?s|what\s+is)\s+this\b", re.I),
    re.compile(r"\bwhat\s+am\s+i\s+looking\s+at\b", re.I),
    re.compile(r"\blook\s+at\s+(this|that|here|it|my)\b", re.I),
    re.compile(r"\b(read|translate|scan)\s+(this|that|it|the|my)\b", re.I),
    re.compile(r"\b(who|what)\s+is\s+(this|that|in\s+front)\b", re.I),
    re.compile(r"\b(what\s+do\s+you|can\s+you|do\s+you)\s+see\b", re.I),
]
# Reject the search verbs so "look it up" / "google" can't trigger the camera.
_VISION_BLOCK = re.compile(
    r"\b(look\s+(up|it\s+up|that\s+up)|search|google|find\s+(out|online))\b", re.I
)


def is_vision_intent(text: str) -> bool:
    """True only when the user asked Nemo to LOOK at something via the camera."""
    t = (text or "").strip()
    if len(t) < 4 or _VISION_BLOCK.search(t):
        return False
    return any(p.search(t) for p in _VISION_PATTERNS)


# "Shut up / go to sleep" — end the live session and drop back to wake-word-only
# mode (no more dialog listening). Bare "stop" is deliberately NOT here (it
# collides with "stop the timer"); the user has clear phrases below.
_DEACTIVATE_PATTERNS = [
    # "shut up", "shutup", "shut the fuck/hell up" (≤2 filler words between).
    re.compile(r"\bshut\s*(\w+\s+){0,2}up\b", re.I),
    re.compile(r"\bbe\s+quiet\b", re.I),
    re.compile(r"\bstop\s+(listening|talking)\b", re.I),
    re.compile(
        r"\b(go\s+to\s+sleep|sleep\s+(mode|now)|go\s+(quiet|away|to\s+sleep))\b", re.I
    ),
    re.compile(
        r"\b(deactivat(e|ion)|turn\s+(yourself\s+)?off|power\s+down|stand\s+down)\b",
        re.I,
    ),
    re.compile(r"\bleave\s+me\s+alone\b", re.I),
    re.compile(
        r"\b(that'?s\s+all|we'?re\s+done|i'?m\s+done|goodbye|bye)\b.{0,8}\bnemo\b", re.I
    ),
    # "nemo, stop/shut up/quiet" — but a bare "stop" here must NOT swallow
    # "nemo, stop the timer/alarm/music/recording" (those are actions, not a
    # request to end the session). The negative lookahead keeps the original
    # collision (deliberately excluded for bare "stop" above) from sneaking back
    # in via the nemo-prefixed form.
    re.compile(
        r"\bnemo\b.{0,8}\b(shut\s*up|go\s+to\s+sleep|quiet|"
        r"stop(?!\s+(the\s+)?(timer|alarm|music|song|recording|reminder|"
        r"playback|video|playing|audio|track|podcast|news|noise)s?\b))\b",
        re.I,
    ),
]


def is_deactivate_intent(text: str) -> bool:
    """True when the user told Nemo to stop/sleep — end the session, back to the
    wake word."""
    t = (text or "").strip()
    if len(t) < 3:
        return False
    return any(p.search(t) for p in _DEACTIVATE_PATTERNS)


# Meeting recorder. START hands the phone's mic to a room-tuned recorder; STOP
# finalizes + uploads for transcription; RECALL asks the engine brain (which has
# the dated transcript on disk) to summarize / send it / answer a question.
# STOP is checked before START so "stop recording" never reads as a start.
_RECORD_STOP = re.compile(
    r"\b(stop|end|finish|done(\s+with)?|wrap\s+up)\b[^.?!]*\brecord(ing)?\b", re.I
)
_RECORD_START = re.compile(
    r"\b(start|begin)\s+recording\b|"
    r"\b(record|tape)\b[^.?!]*?\b"
    r"(this|that|us|it|now|meeting|conversation|call|voice|audio|chat|everything)\b",
    re.I,
)
# Recall words must NOT trigger START ("summarize the recording" is recall).
_RECORD_RECALL = re.compile(
    r"\b(summari[sz]e|summary|recap|transcript|what\s+did\s+we|what\s+were\s+we|"
    r"send\s+me|read\s+me|give\s+me|show\s+me|what\s+was)\b[^.?!]*?\b"
    r"(record(ing|ed)?|transcript|talk(ed|ing)?\s+about|discuss(ed|ing)?|"
    r"conversation|meeting)\b",
    re.I,
)


def is_record_stop_intent(text: str) -> bool:
    """True when the user told Nemo to stop the current recording."""
    t = (text or "").strip()
    return len(t) >= 4 and bool(_RECORD_STOP.search(t))


def is_record_start_intent(text: str) -> bool:
    """True when the user asked Nemo to start recording — not stop or recall."""
    t = (text or "").strip()
    if len(t) < 4 or _RECORD_STOP.search(t) or _RECORD_RECALL.search(t):
        return False
    return bool(_RECORD_START.search(t))


def is_record_recall_intent(text: str) -> bool:
    """True when the user asked about a past recording (summary/transcript/Q&A)."""
    t = (text or "").strip()
    return len(t) >= 4 and bool(_RECORD_RECALL.search(t))


async def recover_delegate(link, bridge, task: str, orchestrator=None) -> None:
    """Fire the delegate_task the model forgot, then have Nemo acknowledge it."""
    import voice_brain

    args: dict = {"task": task}
    if orchestrator is not None:
        args["_voice_session_id"] = orchestrator.session_id
        args["_voice_rev"] = orchestrator.snapshot.rev
    try:
        await voice_brain.dispatch("delegate_task", args, bridge)
    except Exception:  # noqa: BLE001 — never crash the turn over a recovery
        return
    # Land at a conversation gap, atomically (drop if the session closed first).
    await link.inject_text_when_idle(
        "[You were asked to run code but didn't hand it off — it's now running on "
        "your engine brain. Tell Avazbek briefly you're on it and will have the "
        "result shortly.]"
    )
