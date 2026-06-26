# P3 — Async weave-in (stream the background brain into the live voice)

**Goal:** the background model's results **stream back and get woven into the
conversation at a natural gap**, instead of the user waiting for a whole answer.
This is the Thinking Machines "results stream back, integrated at a moment
appropriate to what the user is doing" behavior — and the single biggest
*perceived*-latency win we can ship.

## Non-goals

- Not full speculative small→large merge yet (that's P4 + needs tuning).
- Not changing *what* the engine computes — only *how/when* its output reaches
  the user's ears.

## Current state (what exists today)

- `qwen_realtime._run_bg_tool()` already runs a slow tool, then calls
  `link.inject_text_when_idle(...)` to land the result at a conversation gap —
  **but only after the whole tool finishes**, and only for `web_search`/`look`.
- `delegate_task` results come back via the engine→phone path
  (`reminders.notify_now`), spoken as a fresh notification — **not** woven into
  the ongoing voice session.
- `QwenLink.respond_to_when_idle()` (already built) gives us the atomic
  "inject at the next idle gap" primitive. `_idle` event tracks speaking state.
- Engine side already has the right hook shape: `engine.submit(on_success=…,
  on_failure=…)` fires when CC finishes a turn (and we added `on_failure`).

So the plumbing for *gap-aware injection* exists; what's missing is
**streaming** (start speaking before the whole answer is ready) and a **direct
results channel** from engine → this voice session (today it detours through the
reminders table + phone push).

## Design

### 1. A results channel: engine → this voice session

Today: engine result → `notify_now` → reminder row → phone. For weave-in we want
the result to reach the **Orchestrator of the originating session**.

- Add a lightweight push: when the engine finishes a delegated turn that
  originated from voice, it POSTs chunks to a voice-server endpoint
  (`POST /internal/brain_result`, authenticated, loopback) keyed by
  `session_id` (from the StateSnapshot, P2). The Orchestrator routes them to
  `on_background_chunk`.
- Keep the **existing phone-push path as fallback** when the voice session has
  already closed (this is the current `_deliver_via_engine` behavior — preserve
  it).

### 2. Streaming, not whole-answer

- The engine streams its answer in **clause-sized chunks** (research: stream
  LLM→TTS in sentence/clause units for sub-300ms time-to-first-audio). Each
  chunk is a `BrainChunk{seq, text, final}`.
- The Orchestrator, at the next idle gap (`respond_to_when_idle`), injects chunk
  text for Qwen to speak; subsequent chunks continue the same spoken turn.
- For the **first** chunk we may speak immediately even mid-conversation if it's
  a direct answer to the current ask; later chunks ride the same turn.

### 3. Interruption-aware injection (the hard part)

Weave-in must yield to the user. Rules:
- If the user starts speaking (`speech_started`, already handled in
  `_barge_in`), **stop injecting** remaining chunks, cancel the in-flight Qwen
  response (`response.cancel`, already done), and stash the un-spoken remainder
  keyed by `rev` (P2).
- When idle returns AND `rev` is unchanged (topic didn't move), resume with a
  brief reattach ("—anyway, on that build:") then the remaining chunks.
- If `rev` changed (user moved on), **drop** the remainder (don't blurt stale
  answers). This reuses the response-id/`rev` discipline already in the pump.

### 4. Dedup vs the current reminder path

While `VOICE_WEAVE_IN` is on, a voice-originated delegate is delivered via the
results channel, **not** also via `notify_now` → phone (or the user hears it
twice). Gate this in `reminders.delegate_task`: if the session is live and
weave-in is on, suppress the phone push; else fall back.

## Milestones

- **P3.1 (M1 in the master plan) — stream a delegated answer into voice.**
  Engine streams clause chunks to `/internal/brain_result`; Orchestrator speaks
  them via `respond_to_when_idle`. Flag `VOICE_STREAM_BRAIN`.
  - *Gate:* time-to-first-spoken-word for a delegated task drops measurably vs
    today; final spoken text == final engine text (no dupes/truncation);
    suite green.
- **P3.2 — interruption-aware weave-in.** Implement stop/stash/resume/drop on
  barge-in keyed by `rev`.
  - *Gate (scripted):* a barge-in mid-weave cancels < 150 ms (research budget),
    never blurts stale chunks after a topic change.
- **P3.3 — dedup + fallback.** Suppress the double phone push when live;
  preserve `_deliver_via_engine` when the session is gone.
  - *Gate:* live session → woven once; killed session → delivered once via phone.

## Validation harness (no device needed for most of it)

- Unit-level: a fake `QwenLink` (already used in `test_messages`) records
  injected chunks; assert ordering, dedupe, stop-on-barge-in, drop-on-rev-change.
- The audio *feel* (does the reattach sound natural) is the only device part.

## Risks / rollback

- Risk: chunk boundaries sound choppy → chunk on clause boundaries, not tokens.
- Risk: race between weave-in and a new user turn → all gated by `rev` + the
  existing `_idle`/send-lock discipline in `QwenLink`.
- Rollback: `VOICE_STREAM_BRAIN=0` → today's whole-answer phone push.

## References

- Streaming LLM→TTS clause chunking; barge-in flush <150 ms, turn-gap
  200–450 ms: https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026/ ·
  https://inworld.ai/resources/best-speech-to-speech-apis
- ConvFill — merging a better answer into ongoing speech without a jarring
  switch: https://arxiv.org/pdf/2511.07397
