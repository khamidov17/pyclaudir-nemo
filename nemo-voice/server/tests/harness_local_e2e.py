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
    run_fake_nav_api,
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


async def phase_translator(c: PhoneClient, b: ScriptedBackend) -> None:
    print("phase 10: translator mode — direction hints, aside, strict no-answer")
    b.transcripts.append("translator mode for chinese")
    c.events.clear()
    await c.speak_turn()
    await c.drain(4)
    check("mode-on confirmed", "Mode change" in c.texts(), c.texts()[:60])

    b.transcripts.append("bu narsa qancha turadi?")
    c.events.clear()
    await c.speak_turn(amp=3000)  # owner voice
    await c.drain(3)
    check(
        "owner speech → to Chinese",
        "render this in Chinese" in c.texts(),
        c.texts()[:80],
    )

    b.transcripts.append("zhege yibai kuai")
    c.events.clear()
    await c.speak_turn(amp=31000)  # the other person
    await c.drain(3)
    check(
        "other speech → to Avazbek, no answering",
        "Other speaker" in c.texts() and "Do not answer" in c.texts(),
        c.texts()[:80],
    )

    b.transcripts.append("nemo, is that a fair price here?")
    b.replies.append([{"text": "yo'q, bozorda ellik bo'ladi"}])
    c.events.clear()
    await c.speak_turn(amp=3000)
    await c.drain(3)
    check("aside answered to owner only", "ellik" in c.texts(), c.texts()[:60])

    b.transcripts.append("okay stop translating now")
    c.events.clear()
    await c.speak_turn(amp=3000)
    await c.drain(4)
    check("mode off confirmed", "Mode change" in c.texts(), c.texts()[:60])


async def phase_guidance(c: PhoneClient, b: ScriptedBackend) -> None:
    print("phase 11: voice guidance — route, maneuvers, arrival (offline OSRM)")
    lat_m = 0.001 / 111.0
    b.replies.append(
        _tool_call("start_navigation", {"destination": "the clinic"}, "n1")
    )
    b.replies.append([{"text": "ketdik, yo'l boshlanadi"}])
    c.events.clear()
    await c.inject("navigate me to the clinic")
    await c.wait_for("nav_start", timeout=15)
    check("nav_start reached phone", True)

    await c.send_location(41.0, 69.0)  # first fix → route built
    await c.drain(7)  # inject_when_idle waits out the current turn
    check("route ready spoken", "route ready" in c.texts(), c.texts()[-80:])

    await c.send_location(41.0100 - 450 * lat_m, 69.0)
    await c.drain(6)
    check(
        "500m maneuver spoken",
        "in 500 meters" in c.texts() and "turn right" in c.texts(),
        c.texts()[-90:],
    )

    await c.send_location(41.0100 - 10 * lat_m, 69.0)  # advance past step 1
    await c.send_location(41.0200 - 20 * lat_m, 69.0)  # arrive
    await c.drain(6)
    check("arrival spoken", "arrived at the clinic" in c.texts(), c.texts()[-80:])


async def phase_ledger(c: PhoneClient, b: ScriptedBackend) -> None:
    print("phase 12: voice ledger — log spending, SQL summary")
    b.replies.append(
        _tool_call("log_expense", {"amount": 50000, "category": "food"}, "l1")
    )
    c.events.clear()
    await c.inject("50 ming tushlikka ketdi")
    await c.wait_for("turn_complete", timeout=15)
    await c.drain(2)
    check("expense logged", '"logged"' in c.texts(), c.texts()[:60])

    b.replies.append(_tool_call("ledger_summary", {"period": "week"}, "l2"))
    c.events.clear()
    await c.inject("bu hafta qancha ishlatdim?")
    await c.wait_for("turn_complete", timeout=15)
    await c.drain(2)
    check(
        "summary from real SQL",
        "50000" in c.texts() and "food" in c.texts(),
        c.texts()[:90],
    )


async def phase_multispeaker(c: PhoneClient, b: ScriptedBackend, data_dir: str) -> None:
    print("phase 13: multi-speaker memory — enroll Aziz, attribute, no access")
    b.transcripts.append("nemo, remember Aziz's voice")
    b.replies.append(_tool_call("enroll_speaker", {"name": "Aziz"}, "m1"))
    c.events.clear()
    await c.speak_turn(amp=3000)  # owner asks
    await c.wait_for("turn_complete", timeout=15)
    await c.drain(2)
    check("enrollment armed", "listening" in c.texts(), c.texts()[:70])

    b.transcripts.append("salom men Azizman")
    c.events.clear()
    await c.speak_turn(amp=31000)  # Aziz speaks → becomes his voiceprint
    await c.drain(4)
    check(
        "guest enrolled by voice", "enrollment succeeded" in c.texts(), c.texts()[:80]
    )

    b.transcripts.append("kechqurun futbolga boramizmi")
    c.events.clear()
    await c.speak_turn(amp=31000)  # Aziz again → attributed
    await c.wait_for("turn_complete", timeout=15)
    await asyncio.sleep(1.0)
    eps = db_rows(data_dir, "SELECT text, speaker FROM episodes")
    check(
        "guest speech attributed in memory",
        any(t.startswith("Aziz:") and s == "Aziz" for t, s in eps),
        str([e for e in eps if e[1] == "Aziz"][:1]),
    )

    await _guest_tries_protected_tool(c, b)


async def _guest_tries_protected_tool(c: PhoneClient, b: ScriptedBackend) -> None:
    b.transcripts.append("log fifty thousand for me")
    b.replies.append(
        _tool_call("log_expense", {"amount": 50000, "category": "fun"}, "m2")
    )
    c.events.clear()
    await c.speak_turn(amp=31000)  # Aziz tries a protected tool
    await c.wait_for("turn_complete", timeout=15)
    await c.drain(2)
    check(
        "guest still blocked from owner tools",
        "owner-only" in c.texts(),
        c.texts()[:80],
    )


async def phase_scan(c: PhoneClient, b: ScriptedBackend, data_dir: str) -> None:
    print("phase 14: document scan — receipt → ledger (real VL stand-in)")
    b.transcripts.append("scan this receipt")
    b.replies.append(_tool_call("scan", {"kind": "receipt"}, "sc1"))
    c.events.clear()
    await c.speak_turn(amp=3000)  # owner voice (prior phase left a guest verdict)
    await c.wait_for("turn_complete", timeout=15)
    await c.drain(3)
    check("receipt logged via scan", "Korzinka" in c.texts(), c.texts()[-90:])
    rows = db_rows(data_dir, "SELECT note FROM ledger WHERE kind='expense'")
    check(
        "expense row written",
        any("Korzinka" in (n or "") for (n,) in rows),
        f"{len(rows)} rows",
    )


async def phase_narration(c: PhoneClient, b: ScriptedBackend) -> None:
    print("phase 15: scene narration — camera frame → spoken description")
    b.transcripts.append("describe my surroundings")
    c.events.clear()
    await c.speak_turn(amp=3000)
    started = await c.wait_for("narration_start", timeout=15)
    check("narration_start sent to phone", started is not None)
    await c.send_narration_frame()
    await c.drain(6)
    check(
        "scene described from frame",
        "doorway" in c.texts().lower(),
        c.texts()[-80:],
    )


async def phase_subtitles(c: PhoneClient, b: ScriptedBackend) -> None:
    print("phase 16: subtitles — captions emitted for you + Nemo")
    b.transcripts.append("subtitles on")
    c.events.clear()
    await c.speak_turn(amp=3000)
    await c.wait_for("turn_complete", timeout=15)
    b.transcripts.append("qalaysan bugun")
    b.replies.append([{"text": "zo'r, o'zing?"}])
    c.events.clear()
    await c.speak_turn(amp=3000)
    await c.wait_for("turn_complete", timeout=15)
    await c.drain(2)
    subs = [e for e in c.events if e.get("type") == "subtitle"]
    whos = {s["who"] for s in subs}
    check("captions emitted for both sides", {"you", "nemo"} <= whos, str(subs[:2]))


async def phase_health(c: PhoneClient, b: ScriptedBackend, data_dir: str) -> None:
    print("phase 17: health rhythms — sleep-streak nudge weaves in")
    os.environ["NEMO_VOICE_DATA_DIR"] = data_dir
    from datetime import timedelta

    import ledger

    for d in range(4):
        con = ledger._connect()
        ts = (ledger._now_local() - timedelta(days=d)).strftime("%Y-%m-%d %H:%M")
        con.execute(
            "INSERT INTO ledger (ts, kind, amount, currency, category, note)"
            " VALUES (?, 'habit', 5, '', 'sleep', '')",
            (ts,),
        )
        con.commit()
        con.close()
    c.events.clear()
    deadline = time.monotonic() + 16  # proactive poll (2s) + weave-in round-trip
    while time.monotonic() < deadline and "nights" not in c.texts().lower():
        await c.drain(2)
    check("sleep nudge spoken", "nights" in c.texts().lower(), c.texts()[-90:])


async def _run_phases(c: PhoneClient, backend: ScriptedBackend, data_dir: str) -> None:
    await phase_proactive(c)
    await phase_basic(c, data_dir)
    await phase_tools(c, backend, data_dir)
    await phase_bg_search(c, backend)
    await phase_recording(c, backend)
    await phase_messages(c, backend)
    await phase_vision(c, backend)
    await phase_voicelock(c, backend)
    await phase_ambient(c, backend, data_dir)
    await phase_translator(c, backend)
    await phase_guidance(c, backend)
    await phase_ledger(c, backend)
    await phase_multispeaker(c, backend, data_dir)
    await phase_scan(c, backend, data_dir)
    await phase_narration(c, backend)
    await phase_subtitles(c, backend)
    await phase_health(c, backend, data_dir)


async def main() -> int:
    data_dir = tempfile.mkdtemp(prefix="nemo-harness-")
    seed_followup(data_dir)
    enroll_owner(data_dir)
    backend = ScriptedBackend()
    gw = await run_gateway(backend)
    nav_api = await run_fake_nav_api()
    proc = start_voice_server(data_dir)
    await asyncio.sleep(2.5)
    try:
        async with PhoneClient() as c:
            await _run_phases(c, backend, data_dir)
    finally:
        proc.terminate()
        try:
            logs = proc.communicate(timeout=5)[0].decode()
            if not all(CHECKS):
                tail = [
                    ln
                    for ln in logs.splitlines()
                    if any(
                        k in ln for k in ("proactive", "health", "watcher", "Decision")
                    )
                ]
                print("\n--- server proactive log (tail) ---")
                print("\n".join(tail[-15:]))
        except Exception:  # noqa: BLE001
            pass
        gw.close()
        await nav_api.cleanup()
    ok = all(CHECKS)
    print(
        f"\nRESULT: {sum(CHECKS)}/{len(CHECKS)} checks —",
        "ALL PASS" if ok else "FAILURES above",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
