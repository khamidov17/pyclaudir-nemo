# Nemo capability roadmap — tiers plan (2026-07-03)

Decided with Avazbek: implement all tiers now EXCEPT self-hosting the Qwen
model on ava-gpu (deferred to later today; gateway code is ready).

## Status legend
✅ done · 🔨 this pass · 📱 needs on-device testing · ⏳ deferred

## Tier 1 — close the loops

| Item | Status | Notes |
|---|---|---|
| APK notification listener | ✅ existed | `NemoNotificationListenerService.kt` + `read_messages` command |
| APK recorder | ✅ existed | `recording_service.dart` handles record_start/stop |
| APK `biometric_check` command | 🔨 | voice-lock second factor → existing `BiometricService` cascade (face → fingerprint → PIN, fails closed) |
| Voiceprint enrollment | ⏳ | needs `pip install resemblyzer` on VPS + 30s wav from Avazbek |
| Self-hosted omni (M4 ops) | ⏳ | later today |

## Tier 2 — change what Nemo is

| Item | Status | Notes |
|---|---|---|
| Full-duplex groundwork | ✅ existed | barge-in on VAD speech_started, listen-while-speaking, weave-in. True overlapping speech generation waits for self-hosted omni. |
| Ambient mode | 🔨 | `VOICE_AMBIENT=1`: turn detection stops auto-responding; owner-verified transcripts are journaled (→ memory v2) but Nemo replies ONLY when addressed by name or asked directly. Spoken toggle. Voice lock makes this safe: stranger speech in the room is never stored. |
| Memory consolidation (“dreaming”) | 🔨 | `consolidate.py`: daily pass over yesterday's episodes → insight facts (patterns, people, mood arcs) written to memory v2 with `source=consolidation`. Triggered from proactive_loop once per local day. |
| Interruption learning | 🔨 | log every proactive delivery + whether Avazbek engaged (spoke within 60s). Per-source engagement rate < 0.25 after ≥4 tries → that source demotes SPEAK→PUSH. Table `interruptions` in memory_v2.db. |

## Tier 3 — new senses

| Item | Status | Notes |
|---|---|---|
| Screen awareness | ✅ existed | `screenshot` phone command (biometric-gated) + vision path |
| Emotion in voice | 🔨 | persona fragment: notice tone (tired/stressed/excited), acknowledge like a friend, never clinically. The omni model hears audio directly — this is prompt-level. |
| Location context | 📱 deferred | needs FusedLocation plumbing + on-device testing; design: location rides the app's auth/status frame, feeds interrupt_policy + geofenced reminders. Do together with next APK session. |

## Security review checklist (done 2026-07-03)

- [x] Ambient transcripts: journaled ONLY for verified speakers (`allow_sensitive`),
      so both STRANGER and unverified-UNSURE speech never reach memory
- [x] biometric_check result only trusted from the authenticated app websocket
      (action_bridge rides the token-authenticated session)
- [x] No new unauthenticated HTTP/WS surface (proactive loop + consolidation are
      internal; omni-gateway has Bearer auth — token REQUIRED before prod)
- [x] Interruption log stores source/decision/outcome only — schema has no
      content column (regression-tested)
- [x] No secrets in committed files; .env untouched
- [x] Injection markers stripped from memory text before prompt weave-in
      (recall._sanitize, mirrors reminders.py's sanitizer)

Known pre-existing (not this pass): `phone_tools._to_command` CCN 11.

## Verification (all green 2026-07-03)

- 377 voice/gateway tests + 644 engine tests
- Local harness: 21/21 (turns, memory, tools, bg search, recording,
  messages bridge, vision, voice lock, ambient, proactive weave-in)
- `ruff` / `mypy` / `lizard` clean on all new modules
- `flutter analyze`: 0 issues in changed app code (pre-existing vosk-plugin
  noise untouched)
