# Voice v2 — engineering plans (interaction + background + shared context)

These are the **engineering-only** pillars we can build *now* to exceed the
Thinking Machines "interaction model" experience — without training a model.
They formalize the seams in the architecture you already have (Qwen voice =
interaction model, Claude engine = background model, memory = shared context).

> Build order, each independently shippable + flag-gated + reversible:
> **P1 system shape → P2 shared context → P3 async weave-in → P4 full-duplex.**
> Language work (UZ/KK) and self-hosting are *separate tracks* (see
> `../voice-architecture-v2.md`); these four are pure engineering.

| Plan | Pillar | Confidence | Touches |
|---|---|---|---|
| this file | **System shape / orchestrator** | High | new `Orchestrator` in voice server |
| [01-shared-context.md](01-shared-context.md) | **Shared context** | High | voice server + engine, `data/` |
| [02-async-weave-in.md](02-async-weave-in.md) | **Async weave-in** | Med-High | `qwen_realtime`, `qwen_link`, engine, `app_api` |
| [03-full-duplex.md](03-full-duplex.md) | **Full-duplex / simultaneous speech** | Medium (device) | Flutter client, `qwen_realtime` |

## Design principles (apply to every plan)

1. **Flag-gated.** Each pillar lands behind an env flag, default **off**, with
   today's behavior as the fallback. No pillar becomes default until its gate
   passes.
2. **Grounded in the real code.** Every design references the actual modules it
   changes — no green-field rewrites.
3. **Pass/fail gate per milestone.** A plan step isn't "done" until its measured
   gate is met (latency target, no-regression test, on-device check).
4. **Don't regress the brain or memory** — those are where we already exceed the
   target; protect them.

## The system shape (Pillar 1)

Today the interaction↔background link is **implicit and one-way**: the voice
model calls `delegate_task` → `reminders.notify_now()` inserts a row + kicks the
engine (`/internal/kick`) → the engine processes a turn → the result is pushed
to the phone. There is no component that *owns* the relationship, the shared
state, or the timing of weaving results back.

**P1 introduces an `Orchestrator`** in the voice server — one object per session
that sits between `qwen_realtime` (interaction) and the engine (background):

```
        ┌──────────────────────── Orchestrator (per session) ───────────────────────┐
        │  • owns the StateSnapshot (P2)                                              │
        │  • routes: Tier-0 (voice) | Tier-1 (fast) | Tier-2 (engine)  [semantic]     │
        │  • schedules background work + weaves results back at a good gap (P3)        │
        │  • tracks turn state for barge-in / simultaneous speech (P4)                │
        └────────────▲───────────────────────────────────────────────▲───────────────┘
                     │ events (transcript, tool calls, audio state)    │ results stream
        ┌────────────┴───────────┐                          ┌──────────┴───────────────┐
        │ interaction (Qwen)      │                          │ background (engine/Claude)│
        │ qwen_realtime._QwenPump │                          │ engine.submit + on_*      │
        └─────────────────────────┘                          └───────────────────────────┘
```

### Why an Orchestrator (vs. today's scattered logic)

- Today the per-turn recovery flags, the sensitive-reply binding, the
  `delegate_task` path, and the idle gating live **inside** `_QwenPump`. That's
  fine for one model, but the v2 seams (routing, weave-in timing, shared state)
  are cross-cutting and need one owner.
- The Orchestrator is **additive**: when `VOICE_ORCHESTRATOR=0` (default at
  first), `_QwenPump` behaves exactly as today. When on, the pump forwards its
  events (user transcript, tool call, audio-start/idle) to the Orchestrator,
  which decides routing + weave-in.

### Orchestrator interface (target)

```python
class Orchestrator:
    def __init__(self, link: QwenLink, snapshot: StateSnapshot, brain: BrainClient): ...

    # interaction model events (called by _QwenPump)
    async def on_partial_transcript(self, text: str) -> None: ...   # P2: feed Thinker
    async def on_final_transcript(self, text: str) -> RouteDecision: ...  # P1: route
    async def on_agent_speaking(self, speaking: bool) -> None: ...  # P3/P4: gap tracking
    async def on_tool_call(self, name: str, args: dict) -> None: ...

    # background results (called by the brain client)
    async def on_background_chunk(self, chunk: BrainChunk) -> None: ...  # P3: weave in
```

`RouteDecision ∈ {TIER0_VOICE, TIER1_FAST, TIER2_ENGINE}` — see
[01-shared-context.md](01-shared-context.md) for the semantic-trigger that
produces it.

### Milestones

- **P1.1** — Add `Orchestrator` + `RouteDecision`, wired behind
  `VOICE_ORCHESTRATOR`. Pump forwards events; Orchestrator initially just
  reproduces today's `delegate_task` behavior (no behavior change).
  - *Gate:* full voice test suite green with the flag both on and off; a
    delegated task still works identically when on.
- **P1.2** — Move the existing per-turn recovery + sensitive-binding logic
  behind the Orchestrator (still behavior-preserving).
  - *Gate:* `test_voice_intent`, `test_messages`, `test_qwen_link` unchanged.

### Risks / rollback

- Risk: the Orchestrator becomes a god-object. Mitigation: it *coordinates*,
  it doesn't *implement* — Thinker/router/weaver are separate collaborators.
- Rollback: `VOICE_ORCHESTRATOR=0` → the pump's current inline path.

## References

- Thinking Machines, Interaction Models — interaction/background split, shared
  context: https://thinkingmachines.ai/blog/interaction-models/
- LTS-VoiceAgent — orchestrator coordinating Thinker/Speaker:
  https://arxiv.org/html/2601.19952v1
- ConvFill — small→large collaboration / seamless hand-off:
  https://arxiv.org/pdf/2511.07397
