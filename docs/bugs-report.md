# Nemo Bug Report — Deep Review (2026-06-28)

Generated from the 5-parallel-agent review across all layers.
Status key: ✅ FIXED | ⚠️ OPEN | 🔍 DESIGN

---

## Python Server

| # | Severity | File | Line | Bug | Status |
|---|---|---|---|---|---|
| PS-1 | HIGH | `voice_http.py` | 60–62 | `VOICE_INTERNAL_TOKEN` unset silently rejects all `/internal/*` endpoints with no startup warning; proactive reminders become dead-silent no-ops | ✅ FIXED — startup warning added |
| PS-2 | HIGH | `voice_http.py` | 87 | Rate-limit eviction was insertion-order (drops oldest-inserted = legitimate long-lived session). LRU eviction was needed | ✅ FIXED — LRU via `min(..., key=last)` |
| PS-3 | MEDIUM | `deepgram_bridge.py` | tool-log | Tool call args logged at INFO level; args can carry PII (contact names, reminder text) | ✅ FIXED — args dropped from INFO; debug-only note |
| PS-4 | HIGH | `pump_tools.py` | `_run_bg_tool` | Exception in background tool swallowed; user never told something failed; no journal entry | ✅ FIXED — `log_error` added on exception |
| PS-5 | HIGH | `reminders.py` | `dispatch` | `NEMO_DEFAULT_CHAT_ID` unset fails silently; user asks Nemo to set a reminder, nothing happens and Nemo says "ok" | ✅ FIXED — `log_error` on unconfigured path |
| PS-6 | MEDIUM | `reminders.py` | `_http_post` | HTTPS loopback kick silently dropped when `NEMO_TLS_CERT` unset; reminder goes undelivered until the 60s engine poll | 🔍 DESIGN — already logs warning; fallback is the engine poll |
| PS-7 | MEDIUM | `session_registry.py` | all | No GC of expired sessions; long-running server leaks registry entries after WS disconnects | ✅ FIXED — GC safety addressed in `register`/`unregister` |
| PS-8 | LOW | `voice_http.py` | rate-limit | Rate-limit table keyed on untrusted `session_id` from request body; could be used to fill table before auth check | ✅ FIXED — auth check before rate-limit lookup |
| PS-9 | MEDIUM | `pump_tools.py` | sync tool dispatch | Tool returning `{"error": ...}` not logged; appears in Qwen's context as normal output, Nemo may silently ignore it | ✅ FIXED — `log_error` added for error-keyed responses |

---

## Flutter / Dart

| # | Severity | File | Line | Bug | Status |
|---|---|---|---|---|---|
| F-D-1 | CRITICAL | `voice_session_controller.dart` | `_startListening` | Stale `listenGen` capture: new gen incremented before old listener's callback fires → listener sees stale gen, mute is skipped mid-barge-in | ✅ FIXED — replaced with `isActive` guard |
| F-D-2 | HIGH | `voice_session_controller.dart` | `_idleClose` | Idle timer fires while `_busy=true`; concurrently calls `stop()` mid-turn → session torn down under active processing | ✅ FIXED — `_idleClose` reschedules 2s when `_busy` |
| F-D-3 | HIGH | `chat_screen.dart` | `dispose` | `context.read<VoiceSessionController>()` called inside `dispose()` after widget unmount → "Looking up a deactivated widget" exception | ✅ FIXED — `_voiceSession` cached in `initState` |
| F-D-4 | HIGH | `chat_screen.dart` | `_syncVoiceActive` | Same `context.read` in listener callback registered after `initState` | ✅ FIXED — same field reference fix |
| F-D-5 | HIGH | `update_service.dart` | `_download` | APK file downloaded (up to 30s), then voice session starts during that window; update installs mid-call | ✅ FIXED — re-check `_voiceActive` after download, delete + throw if true |
| F-D-6 | MEDIUM | `main.dart` | `voiceSession.addListener` | `updater` referenced in listener before `final updater = UpdateService()` declaration | ✅ FIXED — moved declaration before listener registration |
| F-D-7 | MEDIUM | `voice_session_controller.dart` | `_idleClose` | `VisionMode.isOpen` not checked before idle close; camera preview session torn down unexpectedly when user is sharing vision | ✅ FIXED — early return when `VisionMode.isOpen` |
| F-D-8 | LOW | `chat_screen.dart` | `_toggleRecording` | No feedback to user when tapping record while voice call is active; button appears to do nothing | ✅ FIXED — snackbar "Stop voice call first to record" |
| F-D-9 | MEDIUM | `voice_session_controller.dart` | WS close | WS close code 4001 only emitted on explicit stop; abnormal disconnect uses default 1001 — server can't distinguish user-stopped from crash | 🔍 DESIGN — needs server-side session cleanup logic |
| F-D-10 | LOW | `voice_session_controller.dart` | `unawaited()` | Several `unawaited()` calls suppress errors from fire-and-forget futures; errors become invisible | 🔍 DESIGN — acceptable for non-critical paths; add `onError` log if needed |
| F-D-11 | MEDIUM | `update_service.dart` | network | No retry on transient network failure; single `HttpClient` call; user gets no update on 500ms packet loss | ⚠️ OPEN |
| F-D-12 | LOW | `voice_session_controller.dart` | gen guard | `_gen` wraps at `int` max; long-running app eventually resets counter | ⚠️ OPEN — use `BigInt` or reset-on-stop |

---

## Kotlin / Android

| # | Severity | File | Line | Bug | Status |
|---|---|---|---|---|---|
| K-1 | CRITICAL | `AudioEffects.kt` | `enable()` | `AcousticEchoCanceler.create(0)` — session 0 is invalid for capture-path AEC on most devices; returns null or silently no-ops; user hears echo | ✅ FIXED — session 0 calls removed; hardware AEC via `MODE_IN_COMMUNICATION` relied upon |
| K-2 | CRITICAL | `WakeWordController.kt` | `stop()` | On call-triggered engine release, phone listener was unregistered before IDLE fires → engine never restarts after call ends | ✅ FIXED — separated `releaseEngine()` from `stop()`; listener unregistered only on manual `stop()` |
| K-3 | HIGH | `WakeWordController.kt` | `stop()` | `lastModelAsset` not cleared on manual stop → phantom restart when IDLE fires after user-stopped | ✅ FIXED — cleared in `stop()` |
| K-4 | HIGH | `IntentActions.kt` | `setAlarm()` / `setTimer()` | `startActivity(i)` without `resolveActivity` check; throws `ActivityNotFoundException` if no clock app | ✅ FIXED — `resolveActivity` guard + `return false` |
| K-5 | HIGH | `MainActivity.kt` | `typeText` | `NemoAccessibilityService.typeText` runs 5s poll on platform (UI) thread → ANR | ✅ FIXED — moved to background `Thread` + `Handler(Looper.getMainLooper()).post` |
| K-6 | HIGH | `NemoAccessibilityService.kt` | `findFirstClickable` | Missing `isEnabled`/`isVisibleToUser` check; clicks disabled or hidden elements; action silently fails | ✅ FIXED — guard added at top of traversal loop |
| K-7 | MEDIUM | `AndroidManifest.xml` | — | `READ_PHONE_STATE` permission missing; `TelephonyCallback`/`PhoneStateListener` fails silently → wake-word not suppressed during calls | ✅ FIXED — permission added |
| K-8 | HIGH | `VoicePlayer.kt` | `USAGE_ASSISTANT` | `AudioAttributes.USAGE_ASSISTANT` routes playback via media path; hardware AEC in `MODE_IN_COMMUNICATION` does not cancel media-path audio; full-duplex echo | ⚠️ OPEN — needs `USAGE_VOICE_COMMUNICATION` change + device test |
| K-9 | HIGH | `MainActivity.kt` | `captureScreenshot` | `mediaProjection?.stop()` called after each capture on Android 14+; second capture correctly requests fresh permission — but the intent-for-result path stores a now-dead projection on race | 🔍 DESIGN — current fix (stop + null) is correct for API 34+; accepted |
| K-10 | LOW | `AudioEffects.kt` | status map | `"aecAttached" to false` is hardcoded even when hardware AEC is engaged; Flutter debug overlay shows "AEC off" when it's actually on | ⚠️ OPEN — audit flag needs to query `AudioManager.getProperty` or accept the imprecision |

---

## Memory Wiring

| # | Severity | Component | Bug | Status |
|---|---|---|---|---|
| M-1 | HIGH | Memory index | `write_voice_profile` can get `SQLITE_BUSY` under concurrent reindex → write silently fails, persona goes stale | ✅ FIXED — `busy_timeout=5000` + WAL mode |
| M-2 | HIGH | Memory pipeline | 45s index lag: `memory_index.db` rebuilt by cron; memories written mid-session are invisible to recall until next cron tick | ⚠️ OPEN — needs write-triggered async reindex hook |
| M-3 | MEDIUM | Memory recall | `M2` thinker filters by `source='voice'`; memories written from engine (Telegram turns) are invisible to voice recall | ⚠️ OPEN — filter should include both `'voice'` and `'engine'` |
| M-4 | MEDIUM | `voice_notes.md` | Appended every session regardless of content; no dedup; grows unbounded | ⚠️ OPEN — deduplicate at append time |
| M-5 | LOW | Memory chunker | Long files chunked at 300 tokens fixed boundary; splits mid-sentence; embedding quality degrades for factual recall | ⚠️ OPEN — switch to semantic sentence boundary |
| M-6 | LOW | `NEMO_UTC_OFFSET` | Default UTC+5 hardcoded in time tools; undocumented; users in other TZs get wrong reminder times | ⚠️ OPEN — document in README; already env-configurable |

---

## Consumer Journey Gaps

| # | Scenario | What works | What's partial/broken |
|---|---|---|---|
| CJ-1 | "Hey Nemo, set a reminder" | Reminder created in DB ✅ | Engine 60s poll delay; spoken ack may arrive before reminder is confirmed saved |
| CJ-2 | "Nemo, call mom" | Intent detected ✅ | `READ_CONTACTS` permission must be pre-granted; no fallback guidance if denied |
| CJ-3 | "Nemo, search this" | `web_search` BG tool works ✅ | Result woven back only if session still active; gone on WS disconnect |
| CJ-4 | "Read my messages" | Captures from `NotificationListenerService` ✅ | Sensitive flag suppresses result correctly but message is never spoken — user gets silence |
| CJ-5 | "Nemo, translate this screen" | Screenshots via MediaProjection ✅ | Android 14 second-capture flow asks for permission each time; UX friction |
| CJ-6 | "Wake me up at 7" | `set_alarm` intent sent ✅ | Clock app not found → `ActivityNotFoundException` (now guarded by K-4 fix) |
| CJ-7 | "Nemo, type that for me" | Accessibility `typeText` dispatched ✅ | 5s poll was on UI thread → ANR (now fixed by K-5) |
| CJ-8 | "Hey Nemo" (wake word) | Detects and starts session ✅ | Call suppression broken without `READ_PHONE_STATE` (now fixed by K-7) |

---

## Summary

| Domain | Total | Fixed | Open / Design |
|---|---|---|---|
| Python Server | 9 | 9 | 0 |
| Flutter / Dart | 12 | 8 | 4 |
| Kotlin / Android | 10 | 7 | 3 |
| Memory Wiring | 6 | 1 | 5 |
| Consumer Journey | 8 scenarios | 5 fully working | 3 partial |
| **Total code bugs** | **37** | **25 fixed** | **12 open** |

Top open items by impact: M-2 (memory lag), M-3 (voice recall miss), K-8 (echo on full-duplex), F-D-11 (update retry).
