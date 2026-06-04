"""Cheap intent routing for the Nemo Telegram deployment."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

log = logging.getLogger("pyclaudir.nemo_router")

INTENTS = {
    "SIMPLE_REPLY",
    "MEMORY",
    "REMINDER",
    "WEB",
    "CODEX",
    "DOCUMENT",
    "FULL_NEMO",
}

_URL_RE = re.compile(r"https?://|\.com|\.io|\.ru|\.xyz|\.me|\.cc")

_DIRECT_MAP: dict[str, str] = {
    "hi": "Hey, I'm here.",
    "hey": "Hey, I'm here.",
    "hello": "Hey, I'm here.",
    "yo": "Yo. I'm here.",
    "ok": "Got it.",
    "okay": "Got it.",
    "k": "Got it.",
    "thanks": "Anytime.",
    "thank you": "Anytime.",
    "ty": "Anytime.",
}

_CODING_MARKERS = (
    "codex",
    "repo",
    "github",
    "clone",
    "commit",
    "pull request",
    "pr ",
    "bug",
    "debug",
    "test",
    "pytest",
    "implement",
    "code",
    "cli",
    "mcp",
)

_CLASSIFY_PROMPT = (
    "Classify this owner Telegram message for Nemo. Return exactly one "
    "label and nothing else.\n\n"
    "Labels:\n"
    "SIMPLE_REPLY: greeting, thanks, ok, tiny chat that needs no tools.\n"
    "MEMORY: asks to remember, recall, update personal/project memory.\n"
    "REMINDER: asks for reminders, alarms, schedules, followups.\n"
    "WEB: needs fresh web/current information.\n"
    "CODEX: coding, repo, implementation, debugging, tests, CLI, MCP.\n"
    "DOCUMENT: document, file rendering, PDF, spreadsheet, presentation.\n"
    "FULL_NEMO: nuanced conversation or anything uncertain.\n\n"
    "Message:\n{text}\n\nLabel:"
)


@dataclass(frozen=True)
class RouterConfig:
    claude_bin: str
    model: str
    enabled: bool = True
    direct_replies: bool = True
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class RouteDecision:
    intent: str
    direct_reply: str | None = None
    reason: str = ""


@dataclass
class _HaikuProcess:
    process: asyncio.subprocess.Process
    stdin: asyncio.StreamWriter
    stdout: asyncio.StreamReader

    def kill(self) -> None:
        try:
            self.process.kill()
        except OSError:
            pass


class NemoRouter:
    """Route owner messages before the main Claude Code worker sees them."""

    def __init__(self, config: RouterConfig) -> None:
        self.enabled = config.enabled
        self.claude_bin = config.claude_bin
        self.model = config.model
        self.direct_replies = config.direct_replies
        self.timeout_seconds = config.timeout_seconds
        self._proc: _HaikuProcess | None = None

    async def route(self, text: str) -> RouteDecision:
        if not self.enabled:
            return RouteDecision("FULL_NEMO", reason="router disabled")

        free = self._free_prefilter(text)
        if free is not None:
            return free

        try:
            return await asyncio.wait_for(
                self._classify_with_haiku(text), timeout=self.timeout_seconds
            )
        except (OSError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            log.warning("router classifier fallback to FULL_NEMO: %s", exc)
            return RouteDecision("FULL_NEMO", reason="classifier fallback")

    def _free_prefilter(self, text: str) -> RouteDecision | None:
        if "ANTHROPIC_MAGIC_STRING_" in text:
            log.warning("injection attempt blocked: ANTHROPIC_MAGIC_STRING_ in message")
            return RouteDecision("FULL_NEMO", reason="injection attempt blocked")

        cleaned = re.sub(r"\s+", " ", text.strip().lower())
        if not cleaned:
            return RouteDecision(
                "SIMPLE_REPLY", direct_reply="I'm here.", reason="empty"
            )

        if self.direct_replies and cleaned in _DIRECT_MAP:
            return RouteDecision(
                "SIMPLE_REPLY",
                direct_reply=_DIRECT_MAP[cleaned],
                reason="free exact direct reply",
            )

        if len(text.strip()) < 30 and _URL_RE.search(text):
            return None

        if any(marker in f" {cleaned} " for marker in _CODING_MARKERS):
            return RouteDecision("CODEX", reason="free coding marker")

        return None

    async def _ensure_running(self) -> _HaikuProcess:
        if self._proc is not None:
            return self._proc
        proc = await asyncio.create_subprocess_exec(
            self.claude_bin,
            "--print",
            "--model",
            self.model,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdin is not None
        assert proc.stdout is not None
        self._proc = _HaikuProcess(process=proc, stdin=proc.stdin, stdout=proc.stdout)
        log.debug("haiku subprocess started pid=%s", proc.pid)
        return self._proc

    async def _do_classify(self, hp: _HaikuProcess, text: str) -> str:
        prompt = _CLASSIFY_PROMPT.format(text=text)
        payload = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
        }
        hp.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await hp.stdin.drain()

        output = ""
        while True:
            line = await hp.stdout.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace")
            output += decoded
            try:
                event = json.loads(decoded)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                break
        return output

    async def _classify_with_haiku(self, text: str) -> RouteDecision:
        try:
            hp = await self._ensure_running()
            output = await self._do_classify(hp, text)
        except OSError as exc:
            log.warning("haiku subprocess I/O error, restarting: %s", exc)
            if self._proc is not None:
                self._proc.kill()
                self._proc = None
            hp = await self._ensure_running()
            output = await self._do_classify(hp, text)

        label = self._extract_label(output)
        if label == "SIMPLE_REPLY" and self.direct_replies:
            return RouteDecision(
                label, direct_reply="I'm here.", reason="haiku simple reply"
            )
        return RouteDecision(label, reason="haiku classifier")

    @staticmethod
    def _text_from_event(event: dict) -> str:
        text = str(event.get("result") or event.get("text") or "")
        if text:
            return text
        if not isinstance(event.get("message"), dict):
            return ""
        parts = event["message"].get("content") or []
        return " ".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))

    @staticmethod
    def _first_intent(upper: str) -> str | None:
        return next((i for i in INTENTS if i in upper), None)

    def _extract_label(self, output: str) -> str:
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            intent = self._first_intent(self._text_from_event(event).upper())
            if intent:
                return intent
        return self._first_intent(output.upper()) or "FULL_NEMO"

    #: (pattern_list, model_id) — order matters: checked top-to-bottom.
    _MODEL_OVERRIDES: tuple[tuple[tuple[str, ...], str], ...] = (
        (("use opus", "switch to opus", "opus please", "with opus"), "claude-opus-4-8"),
        (
            ("use sonnet", "switch to sonnet", "sonnet please", "with sonnet"),
            "claude-sonnet-4-6",
        ),
        (
            ("use haiku", "switch to haiku", "haiku please", "with haiku"),
            "claude-haiku-4-5-20251001",
        ),
    )

    def model_for_turn(self, text: str) -> str | None:
        """Detect explicit model override in message. Returns model string or None."""
        lower = text.lower()
        for patterns, model_id in self._MODEL_OVERRIDES:
            if any(p in lower for p in patterns):
                return model_id
        return None

    def strip_model_directive(self, text: str) -> str:
        """Remove the model-override phrase from *text*, return the remainder."""
        lower = text.lower()
        for patterns, _ in self._MODEL_OVERRIDES:
            for phrase in patterns:
                idx = lower.find(phrase)
                if idx != -1:
                    text = text[:idx] + text[idx + len(phrase):]
                    lower = text.lower()
                    break
        return re.sub(r"\s+", " ", text).strip()

    async def close(self) -> None:
        """Kill the persistent haiku subprocess if running."""
        if self._proc is not None:
            self._proc.kill()
            try:
                await self._proc.process.wait()
            except OSError:
                pass
            self._proc = None
            log.debug("haiku subprocess stopped")
