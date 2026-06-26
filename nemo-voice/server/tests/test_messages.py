"""read_messages tool + the privacy-critical non-persistence behavior."""

from __future__ import annotations

import json

import messages
import pump_class
import pump_tools
import qwen_pump
import qwen_realtime


class FakeBridge:
    def __init__(self, result):
        self._r = result
        self.calls: list[str] = []

    async def run(self, cmd, timeout=15.0):
        self.calls.append(cmd)
        return self._r


class FakeLink:
    """Minimal QwenLink surface used by _run_bg_tool."""

    def __init__(self):
        self.injected: list[str] = []
        self.closed = False
        self.sensitive_next = False

    async def wait_until_idle(self):
        return None

    async def inject_text(self, text):
        self.injected.append(text)

    async def inject_text_when_idle(self, text, *, sensitive=False):
        # Mirror QwenLink: returns False if the session closed first; arms the
        # privacy flag under the (here notional) send lock right before sending.
        if self.closed:
            return False
        if sensitive:
            self.sensitive_next = True
        self.injected.append(text)
        return True


# ── messages.dispatch ────────────────────────────────────────────────────────


async def test_no_bridge_errors():
    out = json.loads(await messages.dispatch("read_messages", {}, None))
    assert "error" in out


async def test_phone_error_is_not_no_messages():
    b = FakeBridge({"ok": False, "error": "notification access is off"})
    out = json.loads(await messages.dispatch("read_messages", {}, b))
    assert "off" in out["error"]  # distinct from "nothing new"


async def test_empty_buffer_says_nothing_new():
    b = FakeBridge({"ok": True, "text": "[]"})
    out = json.loads(await messages.dispatch("read_messages", {}, b))
    assert out["result"] == "nothing new"


async def test_parses_buffer_from_text_field():
    entries = [
        {"app": "Telegram", "sender": "Aziz", "text": "dinner tonight?"},
        {"app": "WhatsApp", "sender": "Mom", "text": "call me"},
    ]
    b = FakeBridge({"ok": True, "text": json.dumps(entries)})
    out = json.loads(await messages.dispatch("read_messages", {}, b))
    assert [m["from"] for m in out["messages"]] == ["Aziz", "Mom"]
    assert b.calls == ["read_messages"]


async def test_malformed_payload_is_graceful():
    b = FakeBridge({"ok": True, "text": "not json"})
    out = json.loads(await messages.dispatch("read_messages", {}, b))
    assert out["result"] == "nothing new"


# ── privacy: non-persistence of message summaries ────────────────────────────


async def test_read_messages_marks_reply_sensitive(monkeypatch):
    async def fake_dispatch(name, args, bridge):
        return json.dumps(
            {"messages": [{"app": "Telegram", "from": "Aziz", "text": "hi"}]}
        )

    monkeypatch.setattr(pump_tools.voice_brain, "dispatch", fake_dispatch)
    link = FakeLink()
    await pump_tools._run_bg_tool(link, None, "read_messages", {})
    # The next reply (the summary) is flagged so the pump won't journal it.
    assert link.sensitive_next is True
    assert "messages" in link.injected[0].lower()


async def test_read_messages_closed_session_does_not_persist(monkeypatch):
    delivered = []
    monkeypatch.setattr(
        pump_tools, "_deliver_via_engine", lambda *a: delivered.append(a)
    )

    async def fake_dispatch(name, args, bridge):
        return json.dumps({"messages": [{"text": "private secret"}]})

    monkeypatch.setattr(pump_tools.voice_brain, "dispatch", fake_dispatch)
    link = FakeLink()
    link.closed = True
    await pump_tools._run_bg_tool(link, None, "read_messages", {})
    # Sensitive content must NEVER hit the engine→reminders DB fallback.
    assert delivered == []


async def test_read_messages_is_in_sensitive_tools():
    assert "read_messages" in pump_tools._SENSITIVE_TOOLS


# ── P0 fix: the per-reply sensitive latch (privacy) ──────────────────────────


def _pump(monkeypatch):
    """A _QwenPump on a real QwenLink + fake sockets, with voice_history.add
    recording so we can assert what gets journaled."""
    from qwen_link import QwenLink

    recorded: list = []
    monkeypatch.setattr(
        pump_class.voice_history,
        "add",
        lambda role, text: recorded.append((role, text)),
    )

    class FakeSock:
        async def send(self, raw):
            return None

    link = QwenLink(FakeSock())
    ctx = pump_tools._SessionCtx(client_ws=FakeSock(), bridge=None)
    pump = pump_class._QwenPump(link, ctx)
    return pump, link, recorded


async def test_sensitive_reply_not_journaled(monkeypatch):
    pump, link, recorded = _pump(monkeypatch)
    link.sensitive_next = True  # bg read_messages set this before injecting
    await pump._audio({"delta": "AAA="})  # reply starts → latches sensitive
    assert pump._reply_sensitive is True
    assert link.sensitive_next is False  # consumed by THIS reply
    pump._reply = "you have 3 from Aziz: dinner tonight"
    await pump._complete_turn()
    assert recorded == []  # message content NEVER journaled


async def test_normal_reply_is_journaled(monkeypatch):
    pump, link, recorded = _pump(monkeypatch)
    await pump._audio({"delta": "AAA="})  # not sensitive
    pump._reply = "hey, how's it going"
    await pump._complete_turn()
    assert recorded == [("nemo", "hey, how's it going")]


async def test_interleaved_turn_cannot_consume_the_flag(monkeypatch):
    # The P0 race: an innocent reply is in flight when the bg sets sensitive_next.
    # The innocent reply latched False at ITS start, so it journals normally and
    # the flag survives for the real summary reply.
    pump, link, recorded = _pump(monkeypatch)
    await pump._audio({"delta": "AAA="})  # innocent reply starts (flag not set yet)
    link.sensitive_next = True  # bg sets it mid-innocent-reply
    pump._reply = "the weather looks nice today"
    await pump._complete_turn()
    assert recorded == [("nemo", "the weather looks nice today")]  # journaled
    assert link.sensitive_next is True  # still armed for the actual summary reply


# ── response-id binding (tightens the per-reply privacy flag) ─────────────────


async def test_sensitive_bound_by_response_id(monkeypatch):
    """When Qwen surfaces response ids, sensitivity pins to the exact response
    opened while the flag was set — not 'whatever reply speaks next'."""
    pump, link, recorded = _pump(monkeypatch)
    link.sensitive_next = True  # bg armed it before this response opened
    pump._on_response_created({"response": {"id": "resp_sensitive"}})
    assert link.sensitive_next is False  # consumed at response.created, by id
    await pump._audio({"delta": "AAA=", "response_id": "resp_sensitive"})
    pump._reply = "3 from Aziz: dinner tonight"
    await pump._complete_turn()
    assert recorded == []  # the message summary is not journaled


async def test_interleaved_response_id_is_not_sensitive(monkeypatch):
    """A different response that opens while the flag is NOT set can never be
    mistaken for the sensitive one — it journals normally."""
    pump, link, recorded = _pump(monkeypatch)
    # Sensitive response is created and pinned.
    link.sensitive_next = True
    pump._on_response_created({"response": {"id": "resp_sensitive"}})
    # An UNRELATED reply (different id) is the one that actually speaks first.
    pump._on_response_created({"response": {"id": "resp_other"}})
    await pump._audio({"delta": "AAA=", "response_id": "resp_other"})
    pump._reply = "the weather looks nice today"
    await pump._complete_turn()
    assert recorded == [("nemo", "the weather looks nice today")]  # journaled
    # The sensitive id is still pinned for when its reply does speak.
    assert "resp_sensitive" in pump._sensitive_response_ids
