# Voice JARVIS build — memory v2, persona, proactive, self-hosted omni

Implements SPEC.md milestones M1–M4. Everything is behind flags and off by
default; the live DashScope path is untouched until a flag flips.

## M1 — Memory v2 (`VOICE_MEMORY_V2=1`)

One SQLite DB `data/memory_v2.db` with four tables:

| Table | What it holds |
|---|---|
| `episodes` | every spoken turn (mirrored from voice_history) |
| `facts` | durable facts; contradicted ones get `superseded_by`, never deleted |
| `procedures` | standing instructions ("when X, do Y") |
| `followups` | commitments with due times, extracted from conversation |

Modules (all in `nemo-voice/server/`):
- `memory_store.py` — schema + CRUD
- `memory_search.py` — DashScope embeddings, hybrid cosine+keyword search,
  keyword-only fallback when embeddings are down
- `recall.py` — per-turn context injection with a hard 150ms budget
  (`VOICE_RECALL_BUDGET_MS`); over budget → answer without memory
- `fact_extractor.py` — session-end qwen-flash pass → facts/procedures/
  followups, with duplicate-skip (cos ≥ 0.90) and supersede (cos ≥ 0.75)
- `memory_migrate.py` — one-shot import of `data/memories/*.md` + the voice
  journal, marker-file idempotent, runs on first flagged session

Wired into: `voice_history.add` (episode mirror), `qwen_pump` (session-end
extraction + migration), `voice_brain.build_prompt` (profile block),
`memory_tools` (`remember` dual-writes, `recall` searches v2 first, thinker
context fetcher routes to budgeted recall).

## M2 — Persona

`persona.py` is now the single source of Nemo's character: FRIDAY-style —
casual, warm, playful banter — with explicit Uzbek/English/Russian
code-switching rules. `VOICE_PERSONA_EXTRA` appends ad-hoc traits.
voice_brain composes it; the identity constraints (never "as an AI", brevity,
continuity) are unchanged.

## M3 — Proactive engine (`VOICE_PROACTIVE_V2=1`)

- `watchers.py` — event sources: due follow-ups (from M1) and new ERROR
  entries in the error journal. Inbox/calendar watchers plug into the same
  registry when their data sources exist.
- `interrupt_policy.py` — SPEAK (live session) / PUSH (phone) / DEFER.
  Quiet hours (23:00–08:00 local, `VOICE_QUIET_START/END`) defer everything
  but critical. Unknown severity fails closed to PUSH.
- `proactive_loop.py` — 60s heartbeat started by voice_http: poll → decide →
  deliver (speak via the live orchestrator, push via `reminders.notify_now`).
  Deferred events stay un-acked and re-decide next tick.

## M4 — Self-hosted omni gateway (`nemo-voice/omni-gateway/`)

A websocket server for ava-gpu that speaks the same realtime dialect as
DashScope, so cutover is just `QWEN_REALTIME_URL=ws://<ava-gpu>:8770`
(DashScope stays as fallback).

- `protocol.py` — session state machine, fully tested against a fake backend
- `vad.py` — energy-based endpointing (DashScope did server VAD; self-hosted
  we do it here); swappable for silero later
- `omni_backend.py` — client for vLLM serving Qwen3-Omni-30B-A3B
  (`OMNI_CHAT_URL`) + an OpenAI-style PCM TTS endpoint (`OMNI_TTS_URL`);
  TTS failure degrades to text-only, never kills the turn
- `server.py` — entrypoint, Bearer auth via `OMNI_GATEWAY_TOKEN`

Not yet done (ops, not code): installing vLLM + the model on ava-gpu and
pointing the VPS at it.

## Verification

`uv run pytest nemo-voice/server/tests nemo-voice/omni-gateway/tests` —
352 tests. New modules pass `ruff check`, `ruff format`, `mypy`, and
`lizard -C 10 -L 40 -a 4`.
