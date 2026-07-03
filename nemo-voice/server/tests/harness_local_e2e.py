"""Local end-to-end harness — every voice feature, no cloud, no phone, no VPS.

  fake phone client ──ws── voice server (real subprocess, backend=qwen)
                                 │ QWEN_REALTIME_URL
                           omni-gateway (in-process, scripted brain)

Phases: basic turn+memory → remember/recall tool loop → background web_search
(real DuckDuckGo) → record start/stop intents → messages via action bridge →
vision degradation → proactive weave-in. Prints PASS/FAIL per check.

Run:  uv run python nemo-voice/server/tests/harness_local_e2e.py
Not collected by pytest (starts servers, needs network for the search phase).
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_stack import (  # noqa: E402
    SERVER_DIR,
    PhoneClient,
    ScriptedBackend,
    run_gateway,
    start_voice_server,
)

FACT = "men dark roast kofe yaxshi ko'raman"
CHECKS: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def db_rows(data_dir: str, sql: str) -> list:
    db = Path(data_dir) / "memory_v2.db"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def seed_followup(data_dir: str) -> None:
    os.environ["NEMO_VOICE_DATA_DIR"] = data_dir
    sys.path.insert(0, str(SERVER_DIR))
    import memory_store

    memory_store.add_followup("klinikaga qo'ng'iroq qilish", "2020-01-01 09:00")


def enroll_owner(data_dir: str) -> None:
    """Enroll the harness 'owner voice' (amp=3000) as the voiceprint."""
    os.environ["NEMO_VOICE_DATA_DIR"] = data_dir
    os.environ["VOICE_SPEAKER_LOCK"] = "1"
    os.environ["SPEAKER_EMBEDDER"] = "energy"
    import speaker_gate
    from harness_stack import loud_pcm, silent_pcm

    assert speaker_gate.enroll(loud_pcm(600) + silent_pcm(900))


async def phase_basic(c: PhoneClient, data_dir: str) -> None:
    print("phase 1: basic turn + memory")
    start = time.monotonic()
    await c.inject(f"eslab qol: {FACT}")
    await c.wait_for("turn_complete")
    latency = time.monotonic() - start
    check("reply round-trips", "text" in c.types(), str(sorted(set(c.types()))))
    check("turn latency < 1.5s", latency < 1.5, f"{latency * 1000:.0f}ms")
    await asyncio.sleep(1.0)
    eps = db_rows(data_dir, "SELECT text FROM episodes")
    check("episode persisted", any(FACT in t for (t,) in eps), f"{len(eps)} rows")


def _tool_call(name: str, args: dict, call_id: str) -> list[dict]:
    return [
        {
            "function_call": {
                "name": name,
                "arguments": json.dumps(args),
                "call_id": call_id,
            }
        }
    ]


async def phase_tools(c: PhoneClient, b: ScriptedBackend, data_dir: str) -> None:
    print("phase 2: remember/recall tool loop")
    b.replies.append(_tool_call("remember", {"note": FACT}, "c1"))
    b.replies.append([{"text": "esladim boss"}])
    c.events.clear()
    await c.inject("shuni eslab qol")
    await c.wait_for("turn_complete", timeout=15)
    await c.drain(2)
    facts = db_rows(data_dir, "SELECT text FROM facts")
    check(
        "remember wrote a v2 fact",
        any(FACT in t for (t,) in facts),
        f"{len(facts)} facts",
    )
    check("post-tool reply spoken", "esladim boss" in c.texts(), c.texts()[:60])

    b.replies.append(_tool_call("recall", {"query": "dark roast kofe"}, "c2"))
    c.events.clear()
    await c.inject("kofe haqida nima bilasan?")
    await c.wait_for("turn_complete", timeout=15)
    await c.drain(2)
    check("recall tool returned the fact", "dark roast" in c.texts(), c.texts()[:80])


async def phase_bg_search(c: PhoneClient, b: ScriptedBackend) -> None:
    print("phase 3: background web_search (real DuckDuckGo, talk continues)")
    b.replies.append(
        [
            {"text": "hozir qidiraman"},
            {
                "function_call": {
                    "name": "web_search",
                    "arguments": json.dumps({"query": "Anthropic Claude"}),
                    "call_id": "c3",
                }
            },
        ]
    )
    c.events.clear()
    await c.inject("Anthropic haqida qidirib ber")
    await c.wait_for("turn_complete", timeout=15)
    immediate = c.texts()
    check("immediate reply while searching", "hozir qidiraman" in immediate)
    await c.drain(14)  # bg task → item.create → second response weaves in
    later = c.texts().replace(immediate, "", 1)
    check("background result woven in later", len(later) > 20, later[:80])


async def phase_recording(c: PhoneClient, b: ScriptedBackend) -> None:
    print("phase 4: record start/stop intents (talking keeps working)")
    b.transcripts.append("record this meeting")
    c.events.clear()
    await c.speak_turn()
    started = await c.wait_for("record_start", timeout=15)
    check("record_start sent to phone", started is not None)
    b.transcripts.append("va shu payt gaplashishda davom etamiz")
    await c.speak_turn()
    await c.wait_for("turn_complete", timeout=15)
    check(
        "conversation continues during recording",
        "tushundim" in c.texts(),
        c.texts()[:60],
    )
    b.transcripts.append("okay stop the recording now")
    await c.speak_turn()
    stopped = await c.wait_for("record_stop", timeout=15)
    check("record_stop sent to phone", stopped is not None)


async def phase_messages(c: PhoneClient, b: ScriptedBackend) -> None:
    print("phase 5: read messages via action bridge")
    b.transcripts.append("check my messages please")
    c.events.clear()
    n_actions = len(c.actions)
    await c.speak_turn()
    await c.drain(10)
    check(
        "phone got an action request",
        len(c.actions) > n_actions,
        str([a.get("command", "")[:40] for a in c.actions[n_actions:]]),
    )
    check(
        "rundown mentions the buffer",
        "Aziz" in c.texts() or "futbol" in c.texts(),
        c.texts()[:100],
    )


async def phase_vision(c: PhoneClient, b: ScriptedBackend) -> None:
    print("phase 6: vision (camera round-trip; VL cloud absent → graceful)")
    b.transcripts.append("look at this and tell me")
    c.events.clear()
    n_actions = len(c.actions)
    await c.speak_turn()
    await c.drain(10)
    check(
        "camera action reached phone",
        len(c.actions) > n_actions,
        str([a.get("command", "")[:40] for a in c.actions[n_actions:]]),
    )
    await c.inject("hali ham shu yerdamisan?")
    alive = await c.wait_for("turn_complete", timeout=15)
    check("session survives VL failure", alive is not None)


async def phase_voicelock(c: PhoneClient, b: ScriptedBackend) -> None:
    print("phase 8: voice lock — stranger blocked, owner allowed")
    stranger_call = [
        {
            "function_call": {
                "name": "recall",
                "arguments": json.dumps({"query": "kofe"}),
                "call_id": "s1",
            }
        }
    ]
    b.transcripts.append("tell me his secrets")
    b.replies.append(list(stranger_call))
    c.events.clear()
    await c.speak_turn(amp=31000)  # not the enrolled voice
    await c.wait_for("turn_complete", timeout=15)
    await c.drain(2)
    check("stranger's recall refused", "owner-only" in c.texts(), c.texts()[:80])

    b.transcripts.append("kofe haqida nima bilasan")
    b.replies.append(
        [
            {
                "function_call": {
                    "name": "recall",
                    "arguments": json.dumps({"query": "dark roast kofe"}),
                    "call_id": "s2",
                }
            }
        ]
    )
    c.events.clear()
    await c.speak_turn(amp=3000)  # the enrolled owner voice
    await c.wait_for("turn_complete", timeout=15)
    await c.drain(2)
    check("owner's recall allowed", "dark roast" in c.texts(), c.texts()[:80])


async def phase_ambient(c: PhoneClient, b: ScriptedBackend, data_dir: str) -> None:
    print("phase 9: ambient mode — hears and remembers, replies only when addressed")
    b.transcripts.append("switch to ambient mode")
    await c.speak_turn()
    await c.wait_for("turn_complete", timeout=15)

    b.transcripts.append("bugun stomatologga borishim kerak edi")
    c.events.clear()
    await c.speak_turn()
    await c.drain(4)
    check(
        "unaddressed speech gets no reply", "tushundim" not in c.texts(), c.texts()[:60]
    )
    eps = db_rows(data_dir, "SELECT text FROM episodes")
    check(
        "...but IS remembered",
        any("stomatolog" in t for (t,) in eps),
        f"{len(eps)} episodes",
    )

    b.transcripts.append("nemo, eshityapsanmi meni?")
    c.events.clear()
    await c.speak_turn()
    await c.wait_for("turn_complete", timeout=15)
    check("addressed by name gets a reply", "tushundim" in c.texts(), c.texts()[:60])

    b.transcripts.append("okay ambient off, gaplashamiz")
    await c.speak_turn()
    await c.wait_for("turn_complete", timeout=15)


async def phase_proactive(c: PhoneClient) -> None:
    print("phase 7: proactive follow-up weaves into live conversation")
    c.events.clear()
    await c.drain(8)  # poll interval is 2s; due followup should fire
    check("Nemo spoke up unprompted", "klinika" in c.texts().lower(), c.texts()[:100])


async def main() -> int:
    data_dir = tempfile.mkdtemp(prefix="nemo-harness-")
    seed_followup(data_dir)
    enroll_owner(data_dir)
    backend = ScriptedBackend()
    gw = await run_gateway(backend)
    proc = start_voice_server(data_dir)
    await asyncio.sleep(2.5)
    try:
        async with PhoneClient() as c:
            await phase_proactive(c)
            await phase_basic(c, data_dir)
            await phase_tools(c, backend, data_dir)
            await phase_bg_search(c, backend)
            await phase_recording(c, backend)
            await phase_messages(c, backend)
            await phase_vision(c, backend)
            await phase_voicelock(c, backend)
            await phase_ambient(c, backend, data_dir)
    finally:
        proc.terminate()
        gw.close()
    ok = all(CHECKS)
    print(
        f"\nRESULT: {sum(CHECKS)}/{len(CHECKS)} checks —",
        "ALL PASS" if ok else "FAILURES above",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
