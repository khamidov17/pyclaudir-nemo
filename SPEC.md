# SPEC — Nemo → JARVIS: Personalized Voice Assistant

**Branch:** `voice-fullduplex-memory-ui` (or successor) · **Date:** 2026-07-03
**Decision record from architecture interview. Approved scope for the next build phases.**

## Goal

Evolve Nemo into a deeply personalized, FRIDAY-style voice assistant:
deep long-term memory, proactive agency, a consistent bilingual persona,
and real device/world control — phone-first, self-hosted brain.

## Decisions (locked)

| Area | Decision |
|---|---|
| Scope | Evolve Nemo in this repo, not greenfield |
| Client | Android phone first; Mac client is a later phase |
| Voice backend | Qwen Omni. Now: DashScope realtime (already wired). Target: **self-hosted Qwen3-Omni-30B-A3B on ava-gpu (8×L20) via vLLM** behind a realtime gateway that speaks the same protocol, so `qwen_realtime.py` only changes `QWEN_REALTIME_URL` |
| Memory backbone | Upgraded SQLite: **sqlite-vec** index + episodic/semantic/procedural split + automatic fact extraction. gbrain optional later, not in the voice path |
| Memory → turn flow | Both: profile + top facts in session-start system prompt **and** per-turn semantic recall injected as a context item before Qwen responds |
| Persona | FRIDAY base — casual, warm, playful banter — with natural Uzbek/English code-switching matching how the user speaks |
| Proactive triggers | Follow-ups extracted from memory, message/inbox awareness, calendar & routine anomalies, system/infra alerts |
| Latency SLA | **< 1.5s p90** user-stops-talking → Nemo-starts-speaking; memory injection budget ≤ 150ms |
| Build order | All four pillars, sequentially — each verified end-to-end before the next |

## System architecture

```
Android app (mic/speaker, wake, push)
        │  websocket (audio + events)
        ▼
AlphaVPS 165.140.240.169  — nemo-voice/server
┌─────────────────────────────────────────────────┐
│ qwen_realtime → qwen_pump → QwenLink            │
│        │                                        │
│   Orchestrator ── semantic_router (T0/T1/T2)    │
│        │                ├─ T2 → Engine (CC)     │
│   capabilities registry ┴─ tools: memory,       │
│     phone, messages, reminders, vision, web     │
│                                                 │
│ MEMORY (this spec, M1)                          │
│   memory_store.py  — sqlite-vec, 3 stores       │
│   fact_extractor.py— post-session extraction    │
│   recall.py        — per-turn injection ≤150ms  │
│                                                 │
│ PROACTIVE (M3)                                  │
│   followups.py, watchers/ (inbox, calendar,     │
│   infra), interrupt_policy.py → push/speak      │
└─────────────────────────────────────────────────┘
        │  realtime ws (same protocol as DashScope)
        ▼
ava-gpu (8×L20)  — M4
  vLLM: Qwen3-Omni-30B-A3B  +  omni_gateway.py
  (streaming audio-in/audio-out, DashScope-shaped events)
```

## Data flow (one voice turn)

1. Phone streams mic audio → server pump → Qwen realtime socket.
2. Qwen VAD detects end of speech; transcript event arrives.
3. `recall.py` runs semantic search over the three stores (≤150ms budget,
   skipped on timeout); relevant memories injected as a context item via
   `QwenLink` before `response.create`.
4. Qwen streams audio back → phone plays it. Tool calls dispatch through the
   capabilities registry; TIER2 utterances route to the engine.
5. Turn transcript appended to the **episodic** store immediately.
6. On session end (and every N turns), `fact_extractor.py` runs an LLM pass
   over new episodes → writes/updates **semantic** facts (about the user,
   people, preferences) and **procedural** notes (how they like things done),
   with dedup against existing facts. Commitments ("I'll call the clinic
   Tuesday") become follow-up rows with due times.

## Memory schema (SQLite, one DB, sqlite-vec)

```
episodes(id, session_id, ts, role, text, embedding)      -- what happened
facts(id, subject, predicate, text, confidence,
      source_episode_id, created_ts, updated_ts,
      superseded_by, embedding)                          -- about the user/world
procedures(id, trigger, text, embedding, updated_ts)     -- how to behave
followups(id, text, due_ts, status, source_episode_id)   -- commitments
```

Facts are upserted, never blind-appended: extractor checks nearest neighbors
and supersedes contradicted facts (`superseded_by`). Existing
`memory_index.py`/`voice_facts.py` data is migrated in; old cosine-in-Python
search is replaced.

## Milestones (each = one PR, verified before the next)

**M1 — Memory upgrade.** `memory_store.py`, `fact_extractor.py`, `recall.py`;
migrate existing data; wire session-start profile + per-turn injection behind
`VOICE_MEMORY_V2=1`. Capabilities `remember/recall` tools re-point to the new
store.

**M2 — Persona.** `persona.py` spec (FRIDAY-casual, Uzbek/English
code-switching rules, how it addresses the user, humor calibration) composed
into the session prompt from the capabilities registry; persona regression
tests (tone assertions on scripted turns).

**M3 — Proactive engine.** `followups.py` (from M1 commitments),
`watchers/` (inbox — extends existing message-awareness; calendar; infra
alerts for Nova/deploys/GPU jobs), and `interrupt_policy.py` deciding
speak-now vs push-notification vs next-briefing.

**M4 — Self-hosted Omni.** On ava-gpu: vLLM serving Qwen3-Omni-30B-A3B +
`omni_gateway.py` websocket translating to the DashScope realtime event shape.
Cutover = env change (`QWEN_REALTIME_URL`). DashScope stays as fallback
backend. (This also unblocks the Uzbek fine-tune serving decision.)

**M5 — Mac client.** Deferred; out of this spec's scope.

## Interfaces

- `recall.py: async def context_for(transcript: str, budget_ms: int = 150) -> list[str]`
- `fact_extractor.py: async def extract(session_id: str) -> ExtractReport` (facts added/updated/superseded, followups created)
- `omni_gateway`: websocket server, accepts the same `session.update` /
  `input_audio_buffer.append` / `response.create` / `conversation.item.create`
  events `qwen_pump` already sends; emits the same response/transcript events.
- Watchers implement `class Watcher: async def poll() -> list[Event]`;
  `interrupt_policy.decide(event, user_state) -> SPEAK | PUSH | DEFER`.

## Verification (definition of done, per milestone)

1. `ruff check`, `ruff format --check`, `mypy`, `pytest`, `lizard -C 10 -L 40 -a 4` — all green (CLAUDE.md limits: files ≤300 lines, functions ≤40).
2. **Scripted voice-turn harness** (`tests/harness/`): test client speaks
   canned turns at the websocket, asserts on replies — memory recall correct,
   persona tone present, tool calls fired. Runs in CI against a mocked Qwen
   backend; nightly against real backend.
3. **Memory accuracy evals**: seed 50+ known facts, run recall queries,
   score precision/recall; regression-gated (no deploy if score drops).
4. **Latency benchmark**: automated p50/p90 turn latency on every deploy;
   gate at 1.5s p90. Memory injection measured separately at ≤150ms.
5. Deploy via existing rsync+systemd flow to AlphaVPS; feature flags default
   off until the milestone's checks pass in prod.

## Failure handling

- Recall timeout → skip injection, log, answer without memory (never block a turn).
- Fact extractor failure → episodes retained, extraction retried next session.
- ava-gpu gateway down → automatic fallback to DashScope backend.
- Watcher errors → error_journal, never crash the voice loop.
- Interrupt policy fails closed: when unsure, push-notify instead of speaking.

## Out of scope

- Mac/desktop client and speaker/home device.
- gbrain as memory backbone (optional later layer).
- Training the Uzbek fine-tune (separate track; M4 unblocks its serving).
- Home automation / smart-home control.
- Multi-user support — this is single-user by design.
