# Design — Vision capture, scene narration, subtitles, health rhythms

Approved plan of record, 2026-07-03. Five features building on the existing
`look` (Qwen-VL) vision path, the ledger, and the proactive watcher rails.

## 0. Shared: `vision.describe()`

Extract a reusable public helper from `vision._ask_vl`:
`describe(image_b64, prompt, mime, max_tokens) -> str | None`. `scan` and
`narration` call it; `look` keeps its own thin wrapper. No behavior change to
`look`.

## 1. Document / receipt capture — `scan` tool  (server only)

`scan(kind)` — snap the camera, run a structured-extraction VL prompt, and
act on the result:

| kind | extract | action |
|---|---|---|
| `receipt` | vendor, total, currency, category | `ledger.log_expense` auto-logged; speak "logged 85k at Korzinka" |
| `card` (business card) | name, org, phone, email | saved as a memory fact ("Contact: …"); confirm |
| `form` | full text + field labels | read back + offer to help fill |
| `auto` (default) | detect which of the above | route accordingly |

- New module `vision_scan.py`: FUNCTIONS/TOOL_NAMES/dispatch, VL JSON prompt,
  parse + route. Owner-only (writes ledger/memory) → in `_PROTECTED_TOOLS`.
- Reuses the camera bridge exactly like `look`. Degrades: no key / bad parse →
  falls back to a plain description, never crashes.
- Capability entry `scan`.

## 2. Outfit & care-label  (prompt only)

Already handled by `look` ("does this match?", "read this care label"). Add a
one-line persona nudge so Nemo answers these with a real opinion + the
practical bit (wash temperature, colour clash). No new tool.

## 3. Live scene narration — accessibility mode  (server + app)

Toggle ("describe my surroundings" / "scene mode", off: "stop describing").
While on, the app captures a camera frame every ~4s and sends it as a
`{"type":"narration_frame","image_b64":…}` on the authenticated voice ws.
Server throttles + VL-describes each frame and speaks a SHORT delta ("a door
on your left, steps ahead") via the idle-inject rail. Never blocks a turn.

- `narration.py`: toggle intents, `enabled()`, `describe_frame` (throttled,
  skips near-duplicate scenes). State on `_SessionCtx.narrating`.
- Pump: on toggle → `narration_start` / `narration_stop` client events.
- `_recv_client`: route `narration_frame` frames (background task).
- App: `scene_streamer.dart` — periodic `camera` capture on `narration_start`,
  stop on `narration_stop`. Frames never journaled.

## 4. Real-time subtitles — captions to screen  (server + app)

Toggle ("subtitles on" / "caption this", off: "subtitles off"). While on, every
user transcript AND Nemo's reply text is also emitted as a
`{"type":"subtitle","who":…,"text":…}` event for the app to render on screen —
useful in a loud room or for the hard-of-hearing. Pairs with translator mode
(read the translation on screen too).

- `subtitles.py`: toggle intents + `enabled()`. State on `_SessionCtx.subtitles`.
- Pump: emit `subtitle` events alongside `user_transcript` / reply text.
- App: a `subtitles` stream + a simple caption overlay (wired now; full styling
  later). Nothing journaled beyond the normal transcript.

## 5. Health rhythms — habit-streak watcher  (server only)

A proactive watcher over the ledger `habit` rows. Detects patterns worth a
gentle nudge:

- sleep < 6h for ≥ N consecutive nights → "you've slept under 6h for 4 nights"
- no `gym`/`run`/exercise habit logged in ≥ 5 days → "haven't seen a workout
  since Monday"
- (extensible: water, screen-time, whatever he logs)

- `health.py`: `poll_health()` / `ack_health()` registered in
  `watchers.WATCHERS`, low severity, throttled (once/day per pattern). Reads
  ledger habit rows; framed as a friend, never clinical, never medical advice.

## Security / privacy

- `scan` writes (ledger/memory) → owner-only via `_PROTECTED_TOOLS`.
- Narration/subtitle frames & captions ride the authenticated ws only, never
  journaled, per-session state dies with the session.
- Card contacts stored as normal memory facts (already sanitized on weave-in).
- Health nudges are observations from his own logs — no external data.

## Status — ALL BUILT + verified 2026-07-03 (harness 42/42)

## Verification

- Unit: vision_scan routing/parse/degrade; narration throttle + dedupe;
  subtitles toggle + emit; health streak detection + throttle.
- Harness phases: scan-receipt→ledger, narration frame→spoken delta,
  subtitles emit, health nudge fires.
- Gates: ruff/mypy/lizard/pytest + flutter analyze on new Dart.
