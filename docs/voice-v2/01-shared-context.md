# P2 — Shared context (the StateSnapshot bus)

**Goal:** replace today's one-way, lossy hand-off (raw task text shoved into a
reminder row) with a **live, structured, bidirectional context** shared between
the interaction model (Qwen) and the background model (engine) — the "Both
systems share their context" arrow in the Thinking Machines figure.

**Why it beats them here:** their shared context is in-model and they *admit*
long sessions strain it. Ours is **explicit and durable** (it plugs into the
existing `data/memories/` + `voice_journal.jsonl` + `memory_index.db`), so it
survives reconnects and grows without blowing a context window.

## Non-goals

- Not a new database. It's an in-memory per-session object that *references*
  the existing stores.
- Not replacing memory recall — it *orchestrates* it (prefetch + cache).

## Current state (what exists today)

- Hand-off is text-only: `nemo-voice/server/reminders.py::delegate_task()` →
  `notify_now()` → INSERT into `reminders` + `/internal/kick`. The engine never
  receives entities, a plan, or the corrected transcript — just a sentence.
- Memory is shared at the *file* level (`voice_brain._profile()` reads
  `data/memories/`; the engine writes them), but there's no per-turn working
  state.
- `voice_history` holds spoken turns; `memory_index` does semantic recall. Both
  are read on demand, not maintained as a running snapshot.

## Design — the StateSnapshot

One object per session, owned by the Orchestrator (P1), updated continuously:

```python
@dataclass
class StateSnapshot:
    session_id: str
    corrected_text: str = ""          # phonetically/grammatically sanitized ASR
    entities: dict[str, Any] = field(default_factory=dict)  # names, numbers, times
    plan: list[str] = field(default_factory=list)           # Thinker's candidate steps
    retrieved_memories: list[str] = field(default_factory=list)  # prefetched recall
    route: str = "TIER0_VOICE"        # last RouteDecision
    lang: str = "en"                  # detected language (en/ru/uz/kk) — see lang track
    updated_monotonic: float = 0.0
    rev: int = 0                      # bumped on every write (cheap optimistic-concurrency)
```

### Who writes what (single-writer per field → no locks needed)

| Field | Writer | When |
|---|---|---|
| `corrected_text`, `entities`, `plan`, `retrieved_memories`, `lang` | **Thinker** (Tier-1, async, P3) | during user speech |
| `route` | semantic trigger (below) | on partial/final transcript |
| everything | reset on barge-in / new turn | P4 |

The snapshot is **append/replace, never concurrently mutated by two writers** —
the Orchestrator serializes writes (it's the only caller), so we avoid locking.
`rev` lets a late background result detect "the world moved on" and drop itself
(same idea as the response-id binding already in `qwen_realtime`).

### Semantic trigger (produces `route`)

Replace the current regex intent + VAD with a cheap classifier on the *running*
transcript (LTS-VoiceAgent pattern):

- Input: accumulated partial transcript.
- Output: `RouteDecision` + confidence; fire only above τ≈0.65; **dedupe** so
  the same prefix doesn't re-trigger (prevents jitter).
- Skips disfluencies ("um", "uh") — semantic, not acoustic, boundaries.
- v1 implementation: keep the existing `voice_intent` regexes as the *fallback*
  and add a small classifier (DistilBERT-class, ~5ms) as the primary; flag
  `VOICE_SEMANTIC_TRIGGER`. No training needed for v1 — rules first, classifier
  when we have data.

### Hand-off carries the snapshot (not just text)

`delegate_task` becomes: serialize the relevant slice of `StateSnapshot`
(`corrected_text`, `entities`, `plan`, `retrieved_memories`) and pass it to the
engine alongside the task — so the background model "stands on the shoulders of
the Thinker" instead of re-deriving everything. Transport: extend the
`/internal/kick` payload (already authenticated) or a new
`POST /internal/delegate` carrying the snapshot JSON.

### Engine side

The engine writes *back* into the snapshot via the results channel (P3): partial
findings, a final answer, and any new durable memory it created. The voice side
reads these to weave in (P3) and to keep `retrieved_memories` warm.

## Milestones

- **P2.1** — Define `StateSnapshot` + wire it into the Orchestrator; populate
  `corrected_text`/`entities` from the existing transcript (no Thinker yet).
  - *Gate:* snapshot serializes/deserializes; unit tests on field lifecycle +
    `rev` bump; no voice-suite regression.
- **P2.2** — Extend the delegate hand-off to carry the snapshot slice to the
  engine; engine prompt includes it as structured context (clearly delimited,
  **never** as instructions — reuse the transcript-injection safety rule).
  - *Gate:* a delegated task that references an earlier entity ("send *that* to
    Aziz") resolves the entity from the snapshot, not a re-ask.
- **P2.3** — Add the semantic trigger producing `route` (rules + optional
  classifier), behind `VOICE_SEMANTIC_TRIGGER`.
  - *Gate:* labeled request set routes correctly above an agreed accuracy;
    dedupe prevents re-triggering on the same prefix.

## Security / privacy

- The snapshot may hold sensitive entities → it lives **in memory per session**,
  is **not** journaled, and is dropped on session end (mirrors the
  `read_messages` sensitive-reply rule already in `qwen_realtime`).
- The snapshot slice sent to the engine goes over the existing authenticated
  loopback channel; never logged verbatim.

## Risks / rollback

- Risk: stale entities cause wrong references → `rev` + reset-on-topic-shift.
- Rollback: `VOICE_SHARED_CONTEXT=0` → today's text-only delegate.

## References

- LTS-VoiceAgent — "State Snapshot", semantic triggering, dedupe:
  https://arxiv.org/html/2601.19952v1
- Thinking Machines — shared context: https://thinkingmachines.ai/blog/interaction-models/
