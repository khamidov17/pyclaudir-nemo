# Voice Architecture v2 — "Listen–Think–Speak" Jarvis

> Status: **proposal / plan**. No code in this doc — it's the contract we build
> against. Each milestone below is independently shippable, gated by a concrete
> pass/fail test, and flag-guarded so it can be turned off instantly.

## Honest confidence statement (read this first)

This is **not** a 100%-guaranteed design, and it should not be sold as one. It
is research-grade in its later stages. Confidence, per milestone:

| Milestone | What | Confidence | Why |
|---|---|---|---|
| M1 | Stream the brain's answer into voice | **High** | Standard streaming engineering; no new model behavior |
| M2 | Think-while-listening prefetch | **Med-High** | Async + a cheap model; main risk is wasted prefetch cost |
| M3 | Tier-1 fast reasoner routing | **High** | Just a routing layer over an existing model |
| M4 | Speculative hand-off + semantic trigger | **Medium** | Research-grade; needs on-device tuning so corrections don't sound jarring |

Everything that touches **audio capture/playback is unverifiable off-device** —
it must be validated on a real phone. We ship behind flags and keep the current
half-duplex path as the always-available fallback.

## Why change anything

Today the voice flow is **serial** when the brain is needed:

```
you finish speaking → voice model decides to delegate → Claude COLD-STARTS
→ you wait in (covered) silence → answer is spoken
```

For chit-chat and the quick tools (calc/convert/time/memory) this is already
instant — they never touch Claude. The latency only bites on delegated work,
and it bites because thinking starts *after* you stop and runs *serially*.

The 2026 literature (LTS-VoiceAgent, ConvFill, SHANKS/Chronological Thinking,
SplitReason, DuplexCascade) all converge on one fix: **overlap thinking with
listening and speaking** instead of making the model faster. See `docs/`
references at the bottom.

## Target experience (the bar we're aiming at)

- Replies to normal talk in **< 400 ms** (human turn-taking range).
- "Real" questions answered in **~1 s** by a fast reasoner, not the heavy engine.
- Heavy jobs (code/research) still take real time, but the answer **starts
  streaming as it's produced** and is never preceded by dead air.
- You can **interrupt** naturally (depends on the AEC work, tracked separately).
- **No vendor API key required** — Tier 0 can run on self-hosted Qwen3-Omni.

## The architecture

Three things run in parallel instead of in series:

```
        WHILE YOU ARE TALKING                       WHEN YOU STOP
  ┌─────────────────────────────────┐        ┌─────────────────────────────┐
  │ THINKER  (Tier-1, async)         │        │ SPEAKER                     │
  │  • sanitize the live transcript  │        │  • Tier-0 voice speaks NOW   │
  │  • extract entities              │  ───►  │    off the ready plan        │
  │  • draft a plan                  │        │  • Tier-2 engine refines in  │
  │  • PRE-FETCH memory + likely     │        │    parallel; result merged   │
  │    tools into a STATE SNAPSHOT   │        │    into the ongoing speech   │
  └─────────────────────────────────┘        └─────────────────────────────┘
        ▲ SEMANTIC TRIGGER decides early whether the brain is needed at all
```

### Tiers (reasoning offloading — SplitReason)

| Tier | Model | Handles | Latency |
|---|---|---|---|
| 0 | Qwen Omni (API now → self-hosted Qwen3-Omni later) | chit-chat, the voice-side tools | instant |
| 1 | Fast reasoner (e.g. Claude Haiku) | most "real" questions, the Thinker | ~1 s |
| 2 | Claude Code engine (current brain) | code, research, multi-step, tools, memory writes | seconds+ |

Today *everything real* pays Tier-2 latency. v2 routes most of it to Tier 1.

### The State Snapshot (the bus)

A structured, continuously-updated object shared between layers — instead of
re-passing raw text through a reminder row:

```jsonc
{
  "corrected_text": "...",         // phonetically sanitized transcript
  "entities": { ... },             // task-relevant values extracted live
  "plan": ["step 1", "step 2"],    // Thinker's candidate plan
  "retrieved_memories": [ ... ],   // prefetched, relevant recall
  "route": "tier0|tier1|tier2"     // semantic-trigger decision
}
```

This also plugs into the **existing memory system** — which is an advantage
over end-to-end models, not a liability (explicit recall beats stuffing
everything into one model's context).

## Milestones (build order = impact, each shippable + reversible)

### M1 — Stream the engine's answer into voice  ·  confidence: High
- **What:** instead of waiting for the whole engine result, stream tokens to
  the voice layer and start speaking as they arrive.
- **Why first:** biggest immediate win, smallest blast radius, no new model.
- **Validation gate:** for a delegated task, time-to-first-spoken-word drops
  measurably; the spoken answer matches the final text (no truncation/dupes).
- **Flag / rollback:** `VOICE_STREAM_BRAIN=0` reverts to the current
  whole-answer delivery.

### M2 — Think-while-listening prefetch  ·  confidence: Med-High
- **What:** a cheap Tier-1 Thinker runs *during* your speech and fills the
  State Snapshot (corrected text, entities, plan, prefetched memories).
- **Why:** removes the cold-start; the brain is warm when you stop.
- **Validation gate:** on delegated turns, end-to-end latency drops vs M1;
  prefetch is discarded cleanly when you change topic mid-sentence.
- **Risk:** wasted Tier-1 cost on turns that don't need it → mitigated by M4's
  semantic trigger; until then, cap prefetch to likely-heavy turns.
- **Flag:** `VOICE_THINKER=0`.

### M3 — Tier-1 fast-reasoner routing  ·  confidence: High
- **What:** route "real but not heavy" questions to Haiku (~1 s) instead of the
  full engine; only escalate to Tier 2 when the task is genuinely multi-step.
- **Validation gate:** a labeled set of sample requests routes correctly
  (chit-chat→T0, factual/quick→T1, code/research→T2) above an agreed accuracy.
- **Flag:** `VOICE_TIERS=0` (everything heavy → Tier 2, i.e. today's behavior).

### M4 — Speculative hand-off + semantic trigger  ·  confidence: Medium (research-grade)
- **What:** Tier-0 starts the answer off the plan immediately; Tier-2's precise
  answer is merged into the ongoing speech (seamless when it agrees, a natural
  pivot — "actually, to be exact…" — when it corrects). A lightweight classifier
  fires on *meaning* (skipping "um") to decide routing early.
- **Why last:** highest payoff for "feel", but needs on-device tuning so merges
  don't sound jarring; this is where LTS trades ~5–10% accuracy for latency.
- **Validation gate (on device):** interruption rate of speculative answers is
  low; corrections are rare and sound natural; no double-talk artifacts.
- **Flag:** `VOICE_SPECULATIVE=0`.

## Cross-cutting risks & how we contain them

1. **Audio is unverifiable off-device.** Every audio change ships behind a flag
   with the half-duplex path as fallback; nothing becomes default until tested
   on a real phone.
2. **Speculative corrections sounding weird** (M4) — gated, tunable thresholds,
   off by default until it sounds right to you.
3. **Tier-1 cost** from prefetching — semantic trigger (M4) bounds it; until
   then prefetch only on likely-heavy turns.
4. **Single-turn engine** — already a documented limitation; v2 doesn't worsen
   it (Tier 0/1 absorb most turns).
5. **Regressions** — each milestone keeps the prior behavior one env-flag away.

## Non-goals (explicitly out of scope)

- Training or fine-tuning a speech model from scratch (frontier-lab effort).
- Replacing the Claude brain with a single end-to-end model.
- Matching `TML-Interaction-Small` the *model* (we match the *experience*).

## No-API-key path (parallel track)

- Stand up **Qwen3-Omni (Apache-2.0, open weights)** on a GPU via vLLM and point
  `QWEN_REALTIME_URL` at it. The bridge already speaks the OpenAI-realtime
  protocol, so Tier 0 swaps with config, not a rewrite.
- Tier 1/2 stay Claude (or an open reasoner if/when you want fully owned).

## References

- LTS-VoiceAgent — Listen-Think-Speak, semantic triggering, incremental
  reasoning: https://arxiv.org/html/2601.19952v1
- ConvFill — small→large inference-time collaboration / seamless hand-off:
  https://arxiv.org/pdf/2511.07397
- DuplexCascade — VAD-free full-duplex cascade: https://arxiv.org/pdf/2603.09180
- Full-Duplex-Bench v2 — evaluation: https://arxiv.org/pdf/2510.07838
- FLEXI — full-duplex human-LLM interaction benchmark:
  https://arxiv.org/html/2509.22243v1
- Liberating LLM Capabilities in Full-Duplex Speech Models:
  https://arxiv.org/html/2606.07547
