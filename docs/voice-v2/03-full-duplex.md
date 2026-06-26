# P4 — Full-duplex / simultaneous speech

**Goal:** the user can talk *while* Nemo talks — interrupt mid-sentence, get a
quick "mm-hm" backchannel, redirect — instead of the current walkie-talkie
(mic muted while Nemo speaks). This is the interaction-model "200 ms micro-turns,
simultaneous speech, no VAD harness" behavior, approximated with open parts.

> **Honesty:** this is the one pillar with a hard ceiling. Their model does true
> simultaneous speech *natively*. With Qwen + client AEC we can reach reliable
> **barge-in** (interrupt cleanly) and *near* full-duplex; true talk-over-each-
> other "simultaneous speech" likely needs model-level work (a later, non-
> engineering track). We scope P4 to **what engineering alone can deliver well**.

## Current state (what exists today)

- **Client (`voice_chat_service.dart`)** captures 16 kHz PCM; `setMuted` stops
  *forwarding* mic audio while Nemo speaks (soft half-duplex). The AEC scaffold
  (`AudioEffects.kt` + `kFullDuplex` flag, default off) is already in the repo
  from the earlier work — it routes playback to the comm path + attaches
  `AcousticEchoCanceler` so the open mic doesn't hear Nemo as a barge-in.
- **Server (`qwen_realtime.py`)** already gets `input_audio_buffer.speech_started`
  from Qwen's server-VAD and runs `_barge_in()` → sends `interrupted` +
  `response.cancel`. So **server-side barge-in support already exists**; the only
  thing stopping it is the client muting the mic.
- **Controller (`voice_session_controller.dart`)** ignores barge-in while
  `nemoSpeaking` unless `kFullDuplex` (we already added that gate).

So the pieces are staged; P4 is about making them work *well* and measuring it.

## Design

### Layer 1 — reliable barge-in (engineering-achievable, the priority)

1. **Keep the mic open** while Nemo speaks (`kFullDuplex=true`), relying on
   platform AEC (`AudioEffects.kt`) so his voice isn't self-detected.
2. **Tighten the cancel path to the research budget:** end-of-user-speech →
   flush playback + cancel Qwen response in **< 150 ms**. Today the flush goes
   client→server→`response.cancel`; measure and shave it (flush the local
   player immediately on `speech_started`, don't wait for the server round-trip).
3. **Echo-loop guard:** if AEC is weak on a device and Nemo self-barges, detect
   rapid self-interruptions and fall back to half-duplex automatically (flag
   demotion), logging it — never get stuck cutting himself off.

### Layer 2 — backchannel + micro-turn approximation (stretch)

- Qwen realtime is turn-based under the hood; true 200 ms interleaving isn't
  exposed. Approximate: shorten the server-VAD `silence_duration_ms` and let
  short user utterances during Nemo's speech trigger a fast barge-in, so it
  *feels* responsive. This is tuning, not new capability.
- Genuine simultaneous speech (both talking, live translation style) is flagged
  as **out of scope for engineering** — it needs the interaction model itself.

### Transport note (important for the ceiling)

Research is blunt: **WebRTC** (not a raw WebSocket) is what makes sub-200 ms
barge-in + low jitter reliable. The app currently uses a WS to the voice bridge.
Moving the audio path to WebRTC is a **separate infrastructure milestone** —
worth it for the full-duplex ceiling, but not required for Layer-1 barge-in on a
good connection. We note it here and gate it as `VOICE_WEBRTC` (future).

## Milestones

- **P4.1 — ship reliable barge-in on device.** Turn on `kFullDuplex`, validate
  AEC, tighten the flush path.
  - *Gate (device):* interrupting Nemo cancels his speech < 150 ms; he does
    **not** self-barge on his own voice in a normal room; reply audio stays
    clear.
- **P4.2 — auto-fallback + tuning.** Self-barge detection → demote to half-duplex
  with a log; tune VAD silence for snappy interrupts.
  - *Gate (device):* on a device with weak AEC, it degrades gracefully instead
    of looping.
- **P4.3 (future, infra) — WebRTC transport.** Move the audio path to WebRTC for
  jitter/latency headroom.
  - *Gate:* measured jitter + barge-in latency improve vs WS on a real network.

## Validation

- Layers 1–2 are **device-only** — there is no way to validate echo/latency off
  a phone. Everything ships behind `kFullDuplex` (already wired) so the proven
  half-duplex path is one flag away.
- Add a tiny on-device debug overlay (reuse the controller's `log`) showing:
  barge-in latency, self-barge count, AEC status — so tuning is data-driven.

## Risks / rollback

- Risk (highest): device AEC variance → P4.2 auto-fallback + per-device flag.
- Risk: comm-path playback sounds muffled (a prior commit reverted this once) →
  measured in P4.1's gate; if it regresses audio, keep media-path playback and
  rely on AEC alone.
- Rollback: `kFullDuplex=false` → today's half-duplex (already the default).

## References

- Barge-in budgets (<150 ms flush, 200–450 ms turn-gap), VAD components,
  WebRTC as the real full-duplex transport:
  https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026/ ·
  https://www.spheron.network/blog/webrtc-llm-streaming-voice-agent-gpu-cloud/
- DuplexCascade — VAD-free micro-turn full-duplex: https://arxiv.org/pdf/2603.09180
- Full-Duplex-Bench v2 — how to *measure* full-duplex quality:
  https://arxiv.org/pdf/2510.07838
