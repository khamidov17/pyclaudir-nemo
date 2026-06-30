# Nemo APK Deep Audit Report

Generated: 2026-06-30

---

## Executive Summary

### Total Bugs by Severity

| Severity | Count |
|---|---|
| CRITICAL | 18 |
| HIGH | 72 |
| MEDIUM | 55 |
| LOW | 22 |
| **Total** | **167** |

Additionally: 39 flow-breaking issues, 31 architectural weaknesses, 8 weak code patterns, 6 strong architecture proposals.

### Top 5 Most Dangerous Issues

1. **B-01 / BUG-03 (voice_chat_service.dart:569)** — `dispose()` calls `stop()` fire-and-forget then immediately closes all StreamControllers. In-flight meeting recording uploads hit closed streams, throwing `StateError` and aborting the upload mid-transfer. Affects every session teardown that has recording active.

2. **BUG-001 (voice_http.py:47)** — `VOICE_INTERNAL_TOKEN` unset makes `/internal/proactive` permanently 401, but is not listed as a required env var. A misconfigured deploy silently breaks weave-in and proactive reminders, and the code path is one refactor away from allowing unauthenticated injection of arbitrary spoken text into any live voice session.

3. **BUG-002 (voice_chat_service.dart:487)** — App token sent in plaintext multipart form body during recording upload, inconsistent with every other endpoint. Any TLS-terminating proxy or server-side body log permanently exposes the never-expiring token.

4. **B-02 (voice_session_controller.dart:265)** — Toggle stop→start race: `stop()` sets `_voice._active=false` immediately, `toggle()` sees `isActive=false` and calls `start()`, but the in-flight `stop()` then closes the brand-new WebSocket. New session appears to connect then immediately dies.

5. **BUG-003 (biometric_service.dart:27)** — `_sensitiveVerbs` is an empty set. All phone commands (tap, swipe, type, camera, screenshot, tg_msg, open, ui_tree, read_messages) execute without any user confirmation. A stolen token gives silent, unconfirmed full phone control.

### Overall Health Score by Domain

| Domain | Score (0-10) | Notes |
|---|---|---|
| Voice session lifecycle | 3/10 | Race conditions, zombie audio, broken dispose |
| Wake word / background | 3/10 | Dead after process kill, no audio focus, TOCTOU races |
| Phone command execution | 4/10 | Type mismatch crash, no timeouts, concurrent execution |
| Security | 2/10 | Disabled biometrics, token in body, predictable device ID, TLS downgrade |
| OTA update | 5/10 | Hash constant wrong length, no cleanup, banner logic broken |
| Chat / UI | 5/10 | Stale state, scroll bugs, use-after-dispose |
| Meeting recording | 3/10 | File leaks, WAV race, upload not deleted |
| Vision mode | 4/10 | Camera leak on background, static singleton race |
| Architecture | 3/10 | No session handoff, two audio paths, no command queue |

---

## Critical & High Bugs (fix immediately)

### CRITICAL

---

**[B-01 / BUG-03]** `dispose()` closes StreamControllers without awaiting `stop()`
**File:** `nemo-app/lib/services/voice_chat_service.dart:569`
**Category:** Lifecycle / crash

`dispose()` calls `stop()` fire-and-forget (no await), then immediately closes all five StreamControllers (`_transcripts`, `_audioOut`, `_controls`, `_errors`, `_actions`). `stop()` is async and contains multiple awaits including up to 5 minutes for `_stopMeetingRecordingAndUpload()`. Any `_controls.add('recording_stopped')` or `_errors.add()` that runs after the controllers are closed throws `StateError`.

**Reproduction:** Start a meeting recording during a voice session, then kill the app while upload is in progress.

**Fix:**
```dart
// Option A: disposed guard on every add
if (!_controls.isClosed) _controls.add('recording_stopped');

// Option B: set a _disposed bool at top of dispose(), guard all adds
bool _disposed = false;

@override
void dispose() {
  _disposed = true;
  stop(); // fire-and-forget, but all adds are now guarded
  _transcripts.close();
  _audioOut.close();
  _controls.close();
  _errors.close();
  _actions.close();
  super.dispose();
}
```

---

**[B-02]** Toggle stop→start race: in-flight `stop()` kills newly-opened WebSocket
**File:** `nemo-app/lib/services/voice_session_controller.dart:265`
**Category:** Race condition / state machine

`stop()` sets `_voice._active=false` at its very first line. `toggle()` reads `isActive` synchronously; a second tap while `stop()` is still awaiting sees `isActive=false` and calls `start()`. The in-flight `stop()` then closes the brand-new WebSocket.

**Reproduction:** Tap orb to end session. Immediately (within ~300ms) tap again. New session connects then dies.

**Fix:**
```dart
// Extend _busy to cover stop() as well as start()
Future<void> stop() async {
  if (_busy) return;
  _busy = true;
  try {
    // ... existing stop logic
  } finally {
    _busy = false;
  }
}
```

---

**[B-03]** `_player.underruns` subscription never cancelled — callbacks into disposed ChangeNotifier
**File:** `nemo-app/lib/services/voice_session_controller.dart:95`
**Category:** Memory leak / use-after-dispose

`_wire()` subscribes to `NativeVoicePlayer.instance.underruns` but never saves the `StreamSubscription`. After `dispose()`, any AudioTrack underrun fires `notifyListeners()` on a disposed ChangeNotifier.

**Reproduction:** Dispose `VoiceSessionController`, trigger a playback underrun.

**Fix:**
```dart
// In _wire():
late final StreamSubscription _underrunSub;
_underrunSub = _player.underruns.listen((n) => _add(VoiceLog.underrun(n)));

// In dispose():
_underrunSub.cancel();
```

---

**[B-01 / phone commands]** `_telegramMessage`: `invokeMethod<bool>` receives a `String` from Kotlin — throws `TypeError`
**File:** `nemo-app/lib/services/phone_command_executor.dart:220`
**Category:** Logic / type mismatch crash

`invokeMethod<bool>('openAppByName', ...)` is typed as `bool?` but the Kotlin side returns `String?`. Flutter platform channels cast to the declared generic, throwing `TypeError` at runtime. Every `tg_msg` command crashes silently into the outer catch, returning a raw `TypeError` string instead of a meaningful error.

**Reproduction:** Say "send a message to John saying hello". The MethodChannel call throws `TypeError`.

**Fix:**
```dart
// Change:
final opened = await _intents.invokeMethod<bool>('openAppByName', {'name': 'Telegram'}) != null;
// To:
final label = await _intents.invokeMethod<String>('openAppByName', {'name': 'Telegram'});
if (label == null) return const ActionOutcome.fail('Telegram is not installed');
```

---

**[B-02 / phone commands]** All MethodChannel calls have no timeout — hangs indefinitely
**File:** `nemo-app/lib/services/phone_command_executor.dart:116`
**Category:** No timeout / indefinite hang

Every `invokeMethod` call has no `.timeout()`. If the Android main thread is blocked or the channel is unregistered, the `Future` never completes. The server waits for `action_result` forever.

**Reproduction:** Send any phone command during app startup. Server action goroutine waits forever.

**Fix:**
```dart
await _accessibility.invokeMethod('typeText', {'text': text})
    .timeout(const Duration(seconds: 15),
             onTimeout: () => throw TimeoutException('typeText timed out'));
```

---

**[B-03 / accessibility]** `NemoAccessibilityService.instance` not `@Volatile` — stale reference across threads
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/NemoAccessibilityService.kt:21`
**Category:** Race condition / null dereference

Plain JVM field; writes on the Android main thread may not be visible to reads on the platform thread for an unbounded time. Stale null causes every accessibility command to return false silently; stale non-null after destroy causes calls on a dead service.

**Fix:**
```kotlin
companion object {
    @Volatile var instance: NemoAccessibilityService? = null
}
```

---

**[BUG-001]** `VOICE_INTERNAL_TOKEN` unset = all `/internal/*` endpoints open / permanently broken
**File:** `nemo-voice/server/voice_http.py:47`
**Category:** Authentication bypass

`_verify_internal()` returns `False` when `_INTERNAL_TOKEN` is empty. Not listed in `_check_required_env()`. A misconfigured deploy silently breaks weave-in; the code is one refactor away from being fully open if the return logic is flipped.

**Fix:** Add `VOICE_INTERNAL_TOKEN` to `_check_required_env()`. Fail startup without it.

---

**[BUG-002]** App token sent in plaintext multipart form body during recording upload
**File:** `nemo-app/lib/services/voice_chat_service.dart:487`
**Category:** TLS / token exposure

`_uploadRecording()` puts the long-lived `app_token` into a multipart form field (`fields['token'] = token`). Appears in server-side request-body logs and any TLS-terminating proxy.

**Fix:**
```dart
// Remove: ..fields['token'] = token;
// Add:
..headers['Authorization'] = 'Bearer $token'
```

Also remove the form-field token acceptance path from server-side `_auth()`.

---

**[B-01 / nemo_service]** Thinking state never clears for audio-only replies
**File:** `nemo-app/lib/services/nemo_service.dart:116`
**Category:** State machine stuck

`NemoState` transitions to `thinking` on `send()` and only back to `connected` on a `'message'` frame. Pure TTS-only or action-only replies leave the spinner stuck forever.

**Fix:**
```dart
case 'audio':
  if (_state == NemoState.thinking) _setState(NemoState.connected);
  // ... existing audio handling

case 'action':
  if (_state == NemoState.thinking) _setState(NemoState.connected);
  // ... existing action handling
```

---

**[BUG-01 / main]** Proactive reminder audio fights active voice session — two audio sinks simultaneously
**File:** `nemo-app/lib/main.dart:42`
**Category:** Simultaneous audio conflict

`nemo.audioB64` piped to `VoiceService.playAudio()` (just_audio MP3) fires while `NativeVoicePlayer` (AudioTrack PCM) is active. Both play simultaneously.

**Reproduction:** Start a voice session. Trigger a scheduled server-side reminder.

**Fix:**
```dart
nemo.audioB64.listen((b64) async {
  if (voiceSession.isActive) return; // skip — voice session handles audio
  await voice.playAudio(b64);
});
```

---

**[BUG-02 / main]** Action ID namespace collision between text and voice backends
**File:** `nemo-app/lib/services/phone_action_service.dart:39`
**Category:** Action ID collision

`PhoneActionService` (text backend) and `VoiceSessionController._onAction` (voice backend) share the same `PhoneCommandExecutor` with no ID namespacing. Coincidentally colliding IDs can deliver results to the wrong backend.

**Fix:** Add namespace prefixes at the server side: engine actions use `eng-{uuid}`, voice actions use `vcs-{uuid}`. Add a `source` field to action frames.

---

**[R-02]** Mic stream continues writing to `_recSink` while WAV assembly reads the PCM file — missing fsync
**File:** `nemo-app/lib/services/voice_chat_service.dart:228`
**Category:** Race condition / data corruption

`await sink.flush()` flushes Dart's IOSink buffer to the OS but does NOT fsync to disk. `wav.addStream(File(pcmPath).openRead())` may read zero bytes at the tail of a large buffer.

**Fix:**
```dart
await sink.close();
// Force OS flush before reading:
final raf = await File(pcmPath).open(mode: FileMode.append);
await raf.flush();
await raf.close();
// Now safe to read:
await wav.addStream(File(pcmPath).openRead());
```

---

**[BUG-03 / nemo_service]** `sendWithMedia` loads entire image into memory twice + base64 on UI thread
**File:** `nemo-app/lib/screens/chat_screen.dart:121`
**Category:** Memory / OOM

`readAsBytes()` + `base64Encode()` + JSON encoder allocate three in-memory copies of the image on the UI isolate. A 10 MP photo causes 12–18 MB spike; OOMs on 2 GB devices.

**Fix:**
```dart
final bytes = await compute(_encodeImage, file.path); // off UI thread
Future<String> _encodeImage(String path) async {
  final b = await File(path).readAsBytes();
  return base64Encode(b);
}
```

---

**[V-03]** Camera stays live on `AppLifecycleState.inactive` if `_init()` is mid-flight
**File:** `nemo-app/lib/screens/vision_mode_screen.dart:142`
**Category:** Resource leak / privacy

If the app goes inactive while `_init()` is awaiting `c.initialize()`, the dispose in `didChangeAppLifecycleState` is a no-op (`_controller` is still null). `_init()` resumes, finds `mounted=true`, assigns the controller, and starts the idle timer. Camera hardware is live while the app is backgrounded.

**Fix:**
```dart
bool _disposed = false;

@override
void didChangeAppLifecycleState(AppLifecycleState state) {
  if (state == AppLifecycleState.inactive || state == AppLifecycleState.paused) {
    _disposed = true; // checked after every await in _init()
    // ... existing dispose logic
  }
}

Future<void> _init() async {
  // ...
  await c.initialize();
  if (_disposed || !mounted) { c.dispose(); return; }
  // ...
}
```

---

**[BUG-001 / settings]** `_save()` calls `configure()` but never disconnects/reconnects
**File:** `nemo-app/lib/screens/settings_screen.dart:97`
**Category:** Settings / connection

`configure()` only updates fields. After "Save & Reconnect", the snackbar fires but the old WebSocket with the old token and URL remains open.

**Fix:**
```dart
await nemo.configure(url, token);
nemo.disconnect();
await nemo.connect();
```

---

**[BUG-01 / voice_service]** Thinking state never clears if only an `'audio'` frame arrives
**File:** `nemo-app/lib/services/nemo_service.dart:116`
*(Duplicate root cause, different trigger — both 'audio' and 'action' cases must clear thinking.)*

---

**[BUG-011 / tls]** SSL certificate verification disabled (`CERT_NONE`) for loopback engine kick
**File:** `nemo-voice/server/reminders.py:136`
**Category:** Insecure TLS

`_http_post()` sets `ctx.check_hostname = False` and `ctx.verify_mode = ssl.CERT_NONE` for loopback URLs. Any process on the same box can impersonate the engine endpoint without cert verification.

**Fix:** Use `ssl.create_default_context()` without `CERT_NONE` even for loopback. Load the engine's self-signed cert explicitly with `ctx.load_verify_locations()`.

---

### HIGH

---

**[B-04]** Mute watchdog (15 s) fires before `_maxMuteMs` cap (30 s) — server-side interruption for long replies
**File:** `nemo-app/lib/services/voice_chat_service.dart:92`

`setMuted()` arms a 15-second watchdog but `VoiceSessionController._maxMuteMs` is 30,000 ms. For any reply longer than 15 seconds, the watchdog fires, reopens the mic, and Deepgram VAD hears speaker echo, truncating the response server-side.

**Fix:** Change watchdog duration to `_maxMuteMs + 100ms`. Pass it as a parameter to `setMuted()` or move `_maxMuteMs` to a shared constant.

---

**[B-05]** +1100 ms unmute tail is 100 ms too short — AudioTrack buffer is 1200 ms, not 800 ms
**File:** `nemo-app/lib/services/voice_session_controller.dart:119`

`bufBytes = 24000 * 2 * 6/5 = 57600 bytes`. At 48000 bytes/sec this is 1200 ms, not the 800 ms the comment claims. The 100 ms bleed window causes false barge-ins on phones without hardware AEC.

**Fix:** Change tail constant to at least 1300 ms. Derive it: `bufBytes / bytesPerSec * 1000 + 100`.

---

**[B-06]** Full-duplex AEC bound to session 0 (output mix) instead of capture session; `USAGE_ASSISTANT` does not provide hardware AEC reference
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/AudioEffects.kt:92`

Two layered problems: (1) `AcousticEchoCanceler.create(0)` attaches to the global mix, not the AudioRecord capture session. (2) `USAGE_ASSISTANT` is not the audio mode that hardware AEC looks for; it expects `USAGE_VOICE_COMMUNICATION`.

**Fix:** Obtain AudioRecord session ID from a native AudioRecord (not the Flutter plugin) and pass it to `AcousticEchoCanceler.create(sessionId)`. Switch `VoicePlayer.kt` to `USAGE_VOICE_COMMUNICATION` in full-duplex mode.

---

**[B-07]** `stop()` closes socket AFTER upload completes — WebSocket stays open up to 5 minutes, server audio plays during teardown
**File:** `nemo-app/lib/services/voice_chat_service.dart:534`

`stop()` awaits `_stopMeetingRecordingAndUpload()` (5-minute timeout) before closing the WebSocket. Nemo can continue speaking for up to 5 minutes after the user ends the session.

**Fix:** Close the WebSocket and cancel `_wsSub` before awaiting the upload. Fire upload as a detached background task after socket teardown.

---

**[BUG-01 / wake_word]** `WakeWordEngine` not released when `eng.start()` throws
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/WakeWordController.kt:51`

If `eng.start()` throws, the catch block calls `s.cancel()` but never calls `eng.release()`. Mic/audio resources held by the engine leak until GC.

**Fix:**
```kotlin
catch (e: Throwable) {
    try { eng.release() } catch (_: Exception) {}
    s.cancel()
    // ... toast
}
```

---

**[BUG-02 / wake_word]** `_onNative()` calls `onWakeWord` without checking `_running` — fires after `stop()`
**File:** `nemo-app/lib/services/wake_word_service.dart:52`

`stop()` sets `_running=false` then calls native stop. There is a window where the engine can deliver one more detection event that bypasses the `_running` check.

**Fix:** Add `if (!_running) return;` at the top of `_onNative()`.

---

**[BUG-03 / background]** After OOM kill + service restart, wake word engine is never re-started
**File:** `nemo-app/lib/services/background_service.dart:71`

The foreground service restarts but runs `_noopCallback()`. `WakeWordService.start()` lives on the main isolate which is NOT started on service restart. Wake word is permanently dead after any process kill.

**Fix:** Have `_KeepAliveHandler.onStart()` send a message to the main isolate via `FlutterForegroundTask.sendDataToMain()`, handled in `main.dart` to call `wake.start()`.

---

**[BUG-04 / wake_word]** `WakeWordEngine` holds mic during incoming phone call — no audio focus handling
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/WakeWordController.kt:35`

Android 12+ can revoke the microphone on phone call. The engine's capture thread throws an unhandled exception. `_running` stays `true` in Dart (stuck state). Wake word is dead with no recovery.

**Fix:** Register `AudioManager.OnAudioFocusChangeListener`. On `AUDIOFOCUS_LOSS`, call `engine?.release()` and notify the Dart side to set `_running=false` and re-prompt for permission.

---

**[BUG-05 / voice]** `stop()` and `_onSessionEnded()` called fire-and-forget from sync `_onControl()` — exceptions silently lost
**File:** `nemo-app/lib/services/voice_session_controller.dart:184`

Both return `Future<void>` but are not awaited. If `_wake.start()` throws (e.g., `FlutterSecureStorage` fails), the exception enters an unawaited `Future`. Wake word never re-arms.

**Fix:** Make `_onControl` `async` and `await` both calls inside `try/catch`, or wrap with `unawaited()` plus explicit `.catchError()`.

---

**[BUG-003 / biometric]** Biometric gate permanently disabled — all phone commands execute without confirmation
**File:** `nemo-app/lib/services/biometric_service.dart:27`

`_sensitiveVerbs = <String>{}`. `isSensitive()` always returns `false`. A stolen token gives silent, unconfirmed full phone control including screenshot, camera, tg_msg, type, and open (arbitrary app).

**Fix:** Re-add `'screenshot'`, `'camera'`, `'tg_msg'`, and `'type'` to `_sensitiveVerbs` at minimum.

---

**[BUG-004 / tls]** `debugPrint` leaks full server cert fingerprint on TOFU pin
**File:** `nemo-app/lib/services/secure_net.dart:44`

Line 44 logs the full 64-char SHA-256 fingerprint to `adb logcat` on every first-connect. Line 48 logs the rejected MITM cert fingerprint. Any app with `READ_LOGS` or ADB access can capture it.

**Fix:** Remove fingerprint values from both `debugPrint` calls. Log only `'SecureNet: server cert pinned'` and `'SecureNet: cert rejected'`.

---

**[BUG-005 / tls]** `SecureNet._loaded` async race — concurrent `httpClient()` calls both enter TOFU path
**File:** `nemo-app/lib/services/secure_net.dart:31`

Two concurrent callers both read `_loaded=false`, both enter the TOFU branch, and the last writer's observed cert wins. The stored pin is indeterminate.

**Fix:**
```dart
static Future<void>? _loadFuture;

static Future<HttpClient> httpClient() async {
  if (!_loaded) {
    _loadFuture ??= _doLoad();
    await _loadFuture;
  }
  // ... rest of method
}
```

---

**[BUG-006 / prompt injection]** `delegate_task` prompt injection sanitization is incomplete
**File:** `nemo-voice/server/reminders.py:242`

Strips only `_TASK_DELIM` and triple-backticks. Does not strip Markdown headings, HTML/XML tags, or Unicode direction overrides. Attacker can get Nemo to read a crafted web page and inject arbitrary tool calls into the engine.

**Fix:** Apply `_sanitize_chunk()` to task text. Add a 2000-char hard cap. Use structured JSON fields rather than free-form text injection.

---

**[BUG-007 / prompt injection]** `/internal/proactive` injects reminder text without sanitization
**File:** `nemo-voice/server/voice_http.py:159`

`handle_proactive()` takes raw text from the JSON body and calls `orch.on_background_chunk()` without `_sanitize_chunk()`. An attacker with valid HMAC can inject arbitrary LLM instructions into a live voice session.

**Fix:** Call `_sanitize_chunk(text)` before building the chunk. Enforce a character limit.

---

**[BUG-008 / device_id]** `device_id` generated from timestamp — predictable, not cryptographically random
**File:** `nemo-app/lib/services/voice_chat_service.dart:396`

`'phone-${DateTime.now().millisecondsSinceEpoch}'` — ~86 million possible values per day. A guessed device ID plus stolen token equals an authenticated session.

**Fix:**
```dart
import 'package:uuid/uuid.dart';
final id = Uuid().v4();
```

---

**[BUG-009 / info disclosure]** `device_id` sent in WebSocket URL query parameter — appears in server access logs
**File:** `nemo-app/lib/services/nemo_service.dart:200`

URL query parameters appear in every nginx/aiohttp access log. The `device_id` is the primary device allowlist identifier.

**Fix:** Remove `device_id` from URL query parameters. Send it only in the first `auth` JSON message body.

---

**[BUG-010 / tls downgrade]** Recording upload and update check downgrade to cleartext HTTP when server URL uses `ws://`
**File:** `nemo-app/lib/services/voice_chat_service.dart:477`

`ws://` is converted to `http://`, bypassing TLS pinning for HTTP channels. The bearer token transmits in cleartext.

**Fix:** Always convert `ws://` to `https://` (not `http://`) for HTTP requests. Reject `ws://` at pairing time.

---

**[B-04 / accessibility]** `findFocusedInput`: EditText class-name match returns wrong node
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/NemoAccessibilityService.kt:163`

The class-name fallback returns the first `EditText` in the tree regardless of focus or enabled state. In Telegram, the unfocused search box may be returned instead of the focused message input.

**Fix:** Remove the class-name fallback entirely, or only use it after exhausting focus+editable checks on all children.

---

**[B-05 / accessibility]** `findByText`: partial match checked after children — returns wrong ancestor node
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/NemoAccessibilityService.kt:81`

Parent container's partial `contains()` match fires after child recursion finds nothing. A container labeled "Send message options" is returned instead of the actual Send button child.

**Fix:** Collect all matching nodes depth-first, return the deepest/most-specific match. Or: recurse first; only partial-match the current node if children returned nothing.

---

**[B-06 / accessibility]** `typeText`: `rootInActiveWindow` null when target app hasn't loaded yet
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/NemoAccessibilityService.kt:46`

If Telegram needs more than 2.5 s to cold-start, `rootInActiveWindow` still points to Nemo and `typeText` returns `false` silently, leaving the user stranded in an empty Telegram search.

**Fix:** Poll `rootInActiveWindow?.packageName` until it contains `'telegram'` or a timeout elapses, instead of a hardcoded 2500 ms sleep.

---

**[B09 / intents]** `setAlarm`/`setTimer`: no `<queries>` entry in manifest — intent silently delivered to nowhere on API 30+
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/IntentActions.kt:138`

Without `<queries><intent><action android:name='android.intent.action.SET_ALARM'/></intent></queries>` in `AndroidManifest.xml`, `startActivity` silently does nothing on API 30+. Kotlin returns `true`, Dart says "alarm set", no alarm is created.

**Fix:** Add the `<queries>` entry. Pre-resolve: `if (pm.resolveActivity(intent, 0) == null) return false`.

---

**[B10 / intents]** `listApps`: `queryIntentActivities` with flag `0` returns empty on Android 11+ without `QUERY_ALL_PACKAGES`
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/IntentActions.kt:61`

On API 30+, only apps declared in `<queries>` are visible. "Open Spotify" fails with "no installed app matching Spotify" even when Spotify is installed.

**Fix:** Add `<uses-permission android:name='android.permission.QUERY_ALL_PACKAGES'/>` or enumerate required packages in `<queries>`. Use `PackageManager.MATCH_ALL` flag.

---

**[B11 / notifications]** `isAccessGranted`: package name substring match has false positives
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/NemoNotificationListenerService.kt:150`

`flat.contains(ctx.packageName)` matches debug/staging variants. A granted debug APK causes the production app to report access as granted while never receiving notifications.

**Fix:** Check for the full component name:
```kotlin
flat.contains(ComponentName(ctx, NemoNotificationListenerService::class.java).flattenToString())
```

---

**[B12 / vision]** `VisionMode._active` set in `initState` before camera initializes — `isOpen` returns true prematurely
**File:** `nemo-app/lib/screens/vision_mode_screen.dart:64`

Between `_active = this` in `initState` and `_ready = true` in `_init()`, another `open()` call sees `_active != null` and `isOpen = false`, pushes a second `VisionModeScreen`, and both race for camera hardware.

**Fix:** Set `VisionMode._active = this` at the end of `_init()`, just before `_ready = true`. Or add a guard in `open()` that returns early if `_active != null` (even if not yet ready).

---

**[P-02]** `MediaProjection` reused across captures — `SecurityException` on Android 14; `hasPermission` always false after first capture
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/MainActivity.kt:250`

`doCapture()` stops and nulls `mediaProjection` after each capture. `captureScreenshot()` checks `mediaProjection != null` which is always false. The "has permission" check is dead code; every screenshot shows the permission dialog.

**Fix:** Accept the per-capture permission model and remove the `hasPermission` check, or use `MediaProjection.Callback` to detect invalidation on Android 14+.

---

**[BUG-04 / voice_session]** `turn_complete` handler arms unmute timer BEFORE setting `nemoSpeaking=false`
**File:** `nemo-app/lib/services/voice_session_controller.dart:111`

`nemoSpeaking` stays `true` until the unmute timer fires. A barge-in arriving during the AudioTrack drain window is incorrectly suppressed because the `interrupted` handler checks `nemoSpeaking && !kFullDuplex`.

**Fix:** On `turn_complete`, set `nemoSpeaking = false` immediately. Track the drain window separately with a dedicated `_inDrainWindow` boolean.

---

**[BUG-05 / reminders]** `_kick_engine` spawns daemon thread that does HTTP POST — not safe if engine exits mid-kick
**File:** `nemo-voice/server/reminders.py:160`

Daemon threads are killed mid-request on process exit. Failures are swallowed by a bare `except`. Reminders that fire at shutdown are silently lost.

**Fix:** Use `asyncio.create_task` and `aiohttp` for the kick. Track the task in `_bg_tasks` to prevent GC.

---

**[BUG-06 / ota]** OTA install invoked while voice session is active — WebSocket/mic not closed before APK installer launches
**File:** `nemo-app/lib/services/update_service.dart:83`

`installApk` kills the app. The voice WebSocket stays open on the server (burning Deepgram credits), the mic is held open, and in-progress meeting recording data is lost.

**Fix:** Before `installApk`, `await VoiceSessionController.stop()` and wait for the meeting recording upload to complete. Warn in the UI if a voice session is active.

---

**[N-01 / notifications]** `getParcelableArray` without type parameter — `ClassCastException` on API 33+
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/NemoNotificationListenerService.kt:63`

The deprecated non-typed overload throws or silently returns null on Android 13+. Telegram group messages fall back to summary text ("3 new messages from Aziz") instead of per-message content.

**Fix:**
```kotlin
val messages = if (Build.VERSION.SDK_INT >= 33)
    extras.getParcelableArray(Notification.EXTRA_MESSAGES, Bundle::class.java)
else
    @Suppress("DEPRECATION") extras.getParcelableArray(Notification.EXTRA_MESSAGES)
```

---

**[N-02 / notifications]** OTP regex does not match 9+ digit codes — 8-digit and 9-digit OTPs leak
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/NemoNotificationListenerService.kt:108`

`{3,7}` repetitions caps at 8 total digits. Some banks use 9-10 digit OTPs which are stored unredacted in SharedPreferences.

**Fix:** Expand upper bound to `{3,11}` and add a contiguous-digits pattern up to 12 digits.

---

**[N-03 / notifications]** `@Synchronized` on `push()` does not prevent SharedPreferences `ANR` on notification flood
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/NemoNotificationListenerService.kt:117`

50+ notifications/second from a group chat triggers O(n²) JSON rebuild operations on the main thread. ANR risk.

**Fix:** Move `push()` off the main thread (HandlerThread or coroutine). Debounce or cap the rate. Use write-ahead/append-only approach rather than full-array-rewrite.

---

**[R-04 / recording]** `startRecording()` does not check available disk space — uncaught `FileSystemException`
**File:** `nemo-app/lib/services/recording_service.dart:34`

If the device is out of storage, `_recorder.start()` throws `FileSystemException`. The exception propagates uncaught. The UI shows no recording state. `_currentPath` is already set but `_recording` is false.

**Fix:** Wrap `_recorder.start()` in try/catch, reset `_currentPath = null` on failure, and surface the error via a ChangeNotifier field.

---

**[R-05 / recording]** Groq API key fetched via `http.get()` without TLS pinning
**File:** `nemo-app/lib/services/recording_service.dart:103`

`RecordingService._getGroqKey()` uses bare `http.get()` (not `SecureNet.httpClient()`). On a `ws://` server URL, the request is plain HTTP with the bearer token in the `Authorization` header.

**Fix:** Replace with `IOClient(await SecureNet.httpClient()).get(uri, headers: {'Authorization': 'Bearer $token'})`.

---

**[BUG-02 / pairing]** Token and URL written to storage before connection is verified
**File:** `nemo-app/lib/screens/pairing_screen.dart:47`

Credentials are persisted and navigation proceeds to `ChatListScreen` without any connection attempt. A typo in the URL sets `isPaired=true` permanently. The user has no way back to the pairing screen without clearing app data.

**Fix:** Attempt a test connection (or `/health` HTTP probe) with a timeout before writing credentials. Navigate only on success.

---

**[BUG-04 / main]** `nemo.connect()` called before `VoiceService` audio listener is registered
**File:** `nemo-app/lib/main.dart:43`

`nemo.audioB64.listen()` should be registered before `nemo.connect()`. Audio frames arriving in the first tick after connect may be dropped.

**Fix:** Move `nemo.audioB64.listen()` registration to before `nemo.configure()/connect()`.

---

**[BUG-08 / lifecycle]** No `AppLifecycleState` observer — background/foreground transitions not handled
**File:** `nemo-app/lib/main.dart:72`

No `WidgetsBindingObserver` attached. Mic is never released when the app backgrounds during a voice session. No reconnect triggered on foreground return after network drop.

**Fix:** Add a `WidgetsBindingObserver` to a top-level State. On `paused`: optionally stop `VoiceChatService`. On `resumed`: call `nemo.connect()` if disconnected.

---

**[FLOW-01]** Voice action with 30 s `execute()` blocks pump watchdog — server times out action before result arrives
**File:** `nemo-app/lib/services/phone_action_service.dart`

`_QwenPump._TURN_IDLE_SEC = 6.0` fires a watchdog and sends `turn_complete` while the tg_msg accessibility flow (minimum 7.1 s) is still executing. The server already says "I couldn't complete that" before the action finishes.

**Fix:** Send intermediate "working..." audio chunks to the voice server every 3 s to reset the turn watchdog, or increase `_TURN_IDLE_SEC` for action turns via a server-negotiated signal.

---

## Medium Bugs

---

**[B-08]** `deactivate` signal calls `stop()` unawaited — user can start new session while teardown is in-flight
**File:** `nemo-app/lib/services/voice_session_controller.dart:191`

`_onControl('deactivate')` sets `idleClosed=true` and calls `stop()` without awaiting. UI renders "Paused" immediately. Orb tap starts a new session while `stop()` is still running its async cleanup chain.

**Fix:** Set `_busy=true` synchronously before the unawaited `stop()`. Clear it at the end of `stop()`.

---

**[B-09]** Meeting recording WAV files never deleted on upload failure
**File:** `nemo-app/lib/services/voice_chat_service.dart:459`

`.pcm` file is deleted unconditionally before `_uploadRecording()`. If upload fails, the `.wav` file is left on disk permanently.

**Fix:**
```dart
try {
  await _uploadRecording(wavPath, id, token, serverUrl);
} finally {
  try { await File(wavPath).delete(); } catch (_) {}
}
```

---

**[B-10]** `_idleClose()` sets `idleClosed` AFTER `await stop()` — orb briefly shows "Connecting..." during idle teardown
**File:** `nemo-app/lib/services/voice_session_controller.dart:288`

Fix: Set `idleClosed = true` and call `notifyListeners()` BEFORE `await stop()`.

---

**[B-11]** ~300 ms gap between first audio chunk and `agent_audio_start` signal — mic open while Nemo begins speaking
**File:** `nemo-app/lib/services/voice_session_controller.dart:100`

Fix: On receiving the first `'audio'` message type, proactively mute if not already muted.

---

**[B-12]** `takePicture()` temp files from camera grabs never deleted
**File:** `nemo-app/lib/screens/vision_mode_screen.dart:121`

Fix:
```dart
final file = await c.takePicture();
final bytes = await file.readAsBytes();
await File(file.path).delete();
return base64Encode(bytes);
```

---

**[B-13]** `VisionMode._active` static race — concurrent `open()` calls orphan one screen
**File:** `nemo-app/lib/screens/vision_mode_screen.dart:25`

Fix: Set `_active` optimistically before `Navigator.push`, or add a static `_opening` bool guard.

---

**[BUG-06 / foreground service]** Foreground service not restarted after voice session ends
**File:** `nemo-app/lib/services/voice_session_controller.dart:261`

`stop()` calls `wake.start()` but not `BackgroundWakeWordService.start()`. Android treats the engine as a background process and kills it.

**Fix:** After `wake.start()`, also call `BackgroundWakeWordService.start()`.

---

**[BUG-07 / wake_word]** `dispose()` fires `invokeMethod('stop')` without await — native engine may not release on exit
**File:** `nemo-app/lib/services/wake_word_service.dart:97`

Fix: Call `stop()` explicitly and await it before calling `super.dispose()` in all lifecycle owners.

---

**[BUG-08 / wake_word]** Concurrent calls to `start()` both pass `_running` check before first one sets `_running=true`
**File:** `nemo-app/lib/services/wake_word_service.dart:63`

Fix: Add a `bool _starting` guard, or use a `Completer/Lock` pattern to serialize concurrent `start()` calls.

---

**[BUG-09 / recording]** Meeting recording WAV `IOSink` never closed on write error — file handle + disk space leak
**File:** `nemo-app/lib/services/voice_chat_service.dart:451`

Fix: Use `try/finally` to guarantee `wav.close()` and cleanup of partial files.

---

**[BUG-10 / wake_word]** `WakeWordEngine` captures `VOICE_COMMUNICATION` source — picks up caller's voice as potential wake word
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/WakeWordController.kt:35`

Fix: Check `AudioManager.getMode()` in `_onNative()`; swallow detection if `MODE_IN_CALL` or `MODE_IN_COMMUNICATION`.

---

**[BUG-11 / wake_word]** Mic permission revoked mid-session — `_running` stays `true` in Dart
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/WakeWordController.kt:48`

Fix: Handle errors from `eng.detections` inside the flow collector. Post a native channel event on error so `WakeWordService` can set `_running=false`.

---

**[B14 / phones]** `_dispatch`: `cmd.split(' ')` breaks for commands with multiple consecutive spaces
**File:** `nemo-app/lib/services/phone_command_executor.dart:53`

Fix: `cmd.trim().split(RegExp(r'\s+'))`.

---

**[B15 / phones]** `_setAlarm`: minutes value not validated — accepts 60-999
**File:** `nemo-app/lib/services/phone_command_executor.dart:166`

Fix: `if (minutes < 0 || minutes > 59) return const ActionOutcome.fail('minutes must be 0-59');`

---

**[B17 / biometric]** `BiometricService._sensitiveVerbs` is empty — 60+ lines of dead authenticate code
**File:** `nemo-app/lib/services/biometric_service.dart:27`

See BUG-003 (CRITICAL). Additionally: reset `_failCount` in a static `reset()` called on app init.

---

**[B18 / tg_msg]** 400 ms delay between typing and clicking Send insufficient when Telegram shows link preview
**File:** `nemo-app/lib/services/phone_command_executor.dart:251`

Fix: Increase delay to 800-1200 ms, or implement a retry loop: try `_click('Send')` up to 3 times with 300 ms between attempts.

---

**[B19 / phone actions]** `PhoneActionService._execute`: no concurrency guard — multiple commands execute in parallel
**File:** `nemo-app/lib/services/phone_action_service.dart:25`

Two parallel `tg_msg` executions interleave accessibility gestures and corrupt UI navigation.

Fix: Add a serial queue. Simplest: add a `Completer? _busy` guard, or use `StreamQueue` / rxdart serial execution.

---

**[B20 / notifications]** `getParcelableArray` without class arg — deprecated and crash-prone on API 33+
*(Duplicate of N-01 — apply the same fix.)*

---

**[B21 / ota]** `isSignedByReleaseKey`: SHA-256 hex constant is 63 characters (should be 64) — always returns false
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/IntentActions.kt:122`

`RELEASE_CERT_SHA256` is 63 hex chars; SHA-256 produces 64. The comparison never matches. Every OTA install attempt is rejected.

**Fix:** Recount and correct the constant by verifying against the actual release signing certificate.

---

**[B22 / accessibility]** `clickFirstResult`: `findFirstClickable` may return a section header instead of a result row
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/NemoAccessibilityService.kt:95`

Fix: Filter by `isClickable && isEnabled && isVisibleToUser` and prefer nodes whose className matches RecyclerView row types.

---

**[BUG-012 / timing]** Non-constant-time APK SHA-256 comparison
**File:** `nemo-app/lib/services/update_service.dart:139`

Low exploitability but non-constant-time `!=` operator on SHA-256 strings is a timing oracle.

Fix: Implement a constant-time compare using XOR accumulation over all bytes.

---

**[BUG-013 / storage]** Meeting recording WAV file not deleted after successful upload
**File:** `nemo-app/lib/services/voice_chat_service.dart:450`

*(See also B-09 above for failure case.)* On success path, WAV is also not deleted.

Fix: Delete WAV after successful upload in the `finally` block.

---

**[BUG-014 / storage]** Downloaded APK not deleted from temp directory after successful install hand-off
**File:** `nemo-app/lib/services/update_service.dart:84`

Fix: Delete the file after `invokeMethod('installApk')` returns, both success and failure paths.

---

**[BUG-015 / dos]** `_RATE` defaultdict grows unbounded — memory exhaustion via session_id enumeration
**File:** `nemo-voice/server/voice_http.py:40`

Fix: Add a max dict size (10,000 entries) with LRU eviction, or delete the `_RATE` entry when a session is removed from `session_registry`.

---

**[BUG-016 / storage]** `FlutterSecureStorage` without hardware-backed key binding — keys extractable on rooted devices
**File:** `nemo-app/lib/main.dart:20`

Fix: Add `keyCipherAlgorithm: KeyCipherAlgorithm.RSA_ECB_OAEPwithSHA_256andMGF1Padding` and `requireUserAuthenticationValidityDurationSeconds: 0` to bind the key to TEE/StrongBox.

---

**[BUG-017 / command injection]** `type` command passes raw server text into any focused input field with no sanitization
**File:** `nemo-app/lib/services/phone_command_executor.dart:83`

Fix: Re-add `'type'` to `BiometricService._sensitiveVerbs`. Restrict to known-safe applications by checking foreground package against an allowlist.

---

**[BUG-07 / chat]** `saveMessage()` is not atomic — two separate writes with no transaction
**File:** `nemo-app/lib/services/chat_storage.dart:76`

Fix: Wrap both statements in `db.transaction()`. Same fix for `deleteSession()`.

---

**[BUG-09 / chat]** Auto-rename title check fires incorrectly due to `_messages.length` race with `_loadMessages()`
**File:** `nemo-app/lib/screens/chat_screen.dart:83`

Fix: Check against the session's stored message count or use a separate `_hasAutoRenamed` flag.

---

**[BUG-11 / chat]** `_onNemoReply` calls `setState` without checking `mounted`
**File:** `nemo-app/lib/screens/chat_screen.dart:61`

Fix: Add `if (!mounted) return;` as the first line of `_onNemoReply`.

---

**[BUG-16 / reconnect]** Reconnect timer fires when state is already connecting — duplicate `connect()` calls possible
**File:** `nemo-app/lib/services/nemo_service.dart:216`

Fix: Set `_setState(NemoState.connecting)` before the async gap, or use a mutex/lock guard at the top of `connect()`.

---

**[R-06 / recording]** `RecordingService` opens a second `AudioRecorder` while voice session mic is active
**File:** `nemo-app/lib/services/recording_service.dart:34`

Fix: Add an assertion/guard that rejects `startRecording()` when a voice session is active, or remove `RecordingService` entirely in favor of the tee path.

---

**[R-07 / recording]** No retry on Groq transcription upload timeout — silent data loss
**File:** `nemo-app/lib/services/recording_service.dart:85`

Fix: Add at least one retry with exponential backoff. On final failure, surface an error via ChangeNotifier and log the `audioPath` so the UI can offer a retry.

---

**[BUG-11 / update]** `_updateAvailable` never reset if download is interrupted — update banner loops
**File:** `nemo-app/lib/services/update_service.dart:120`

Fix: On `UpdateException` with 'hash mismatch', set `_updateAvailable=false` to prevent retries without a fresh `checkForUpdate()`.

---

**[BUG-12 / update]** `checkForUpdate()` called twice on paired launch — redundant network call
**File:** `nemo-app/lib/services/update_service.dart:56`

Fix: Remove the `checkForUpdate()` call from `ChatListScreen._initVoice()`. Leave only the delayed one in `main.dart`. Add an `_checked` bool guard in `UpdateService`.

---

**[BUG-19 / voice_chat]** WAV file is never deleted after successful upload
*(Duplicate across multiple audit sections — consolidated above.)*

---

**[BUG-23 / wav]** `_wavHeader` receives potentially incorrect byte count — should read actual file size after sink close
**File:** `nemo-app/lib/services/voice_chat_service.dart:507`

Fix: After `sink.close()`, read the actual file size: `final dataLen = await File(pcmPath).length();`

---

**[BUG-16 / settings]** `_toggleWake`: `BackgroundWakeWordService.start()` called without correct `statusText`
**File:** `nemo-app/lib/screens/settings_screen.dart:53`

Fix: Pass `statusText: 'Listening for "Hey Nemo"…'` to `BackgroundWakeWordService.start()` when `on=true`.

---

## Low / Polish Issues

---

**[B-14]** `VoicePlayer.stop()`: `t.release()` skipped if `t.pause()` or `t.flush()` throws
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/VoicePlayer.kt:137`

Fix: Wrap each call in separate try/catch; always call `t.release()` in a `finally`.

---

**[B-15]** `_errors.add()` in recorder `onError` callback unguarded against closed controller
**File:** `nemo-app/lib/services/voice_chat_service.dart:239`

Fix: `if (!_errors.isClosed) _errors.add('Microphone stream failed...');`

---

**[B-16]** Class docstring says `USAGE_MEDIA` but code uses `USAGE_ASSISTANT`
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/VoicePlayer.kt:16`

Fix: Update the KDoc to say `USAGE_ASSISTANT`. Delete the contradictory paragraph.

---

**[B-17]** AudioTrack underrun fires at every session start — player opened before data arrives
**File:** `nemo-app/lib/services/voice_session_controller.dart:234`

Fix: Start the AudioTrack in PAUSED state; call `play()` only when the first audio chunk arrives.

---

**[BUG-12 / activity]** `WakeWordController` bound to `MainActivity`-scoped `MethodChannel` — breaks if engine is recreated
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/MainActivity.kt:168`

Fix: Ensure `cleanupFlutterEngine()` override also calls `wakeWord?.stop()` to handle partial detach scenarios.

---

**[BUG-13 / kFullDuplex]** `kFullDuplex` is a compile-time constant — future toggle requires full rebuild
**File:** `nemo-app/lib/services/voice_chat_service.dart:27`

See WP-1 and SP-3 for the full runtime-config architecture.

---

**[B23 / phones]** `list_apps`: `cast<String>()` throws if platform channel encodes as `List<dynamic>`
**File:** `nemo-app/lib/services/phone_command_executor.dart:98`

Fix: `apps.whereType<String>().join('\n')`.

---

**[B24 / phones]** `_captureCamera`: `context.mounted` not re-checked after `VisionMode.open()` awaits
**File:** `nemo-app/lib/services/phone_command_executor.dart:317`

Fix: Add a `mounted` check after the await as defensive programming, with a comment explaining the synchronous context use.

---

**[BUG-018 / logs]** Voice function call arguments logged at INFO level — may contain personal data
**File:** `nemo-voice/server/deepgram_bridge.py:38`

Fix: Redact args for sensitive function names (`remember`, `send_telegram`, `search_chat`). Log args at DEBUG level only.

---

**[BUG-019 / logs]** Memory notes logged at INFO level — personal data in server logs
**File:** `nemo-voice/server/voice_brain.py:364`

Fix: Drop to `LOG.debug()` or remove the log entirely.

---

**[BUG-020 / pairing]** `ws://` allowed at pairing time — defeats TLS enforcement downstream
**File:** `nemo-app/lib/screens/pairing_screen.dart:63`

Fix: Reject `ws://` at pairing time with "Use wss:// for encrypted connection". Gate plaintext behind a dev build flag.

---

**[BUG-24 / wake_word]** `WakeWordService.stop()` sets `_running=false` before native stop completes
**File:** `nemo-app/lib/services/wake_word_service.dart:87`

Fix: Move `_running=false` and `notifyListeners()` to AFTER `await _channel.invokeMethod('stop')` in a `try/finally`.

---

**[BUG-25 / token]** `_wsUri()` copies all query parameters including any accidental token parameters
**File:** `nemo-app/lib/services/nemo_service.dart:198`

Fix: Filter out `'token'` and `'auth'` keys: `...Map.fromEntries(base.queryParameters.entries.where((e) => e.key != 'token' && e.key != 'auth'))`.

---

**[BUG-26 / model]** `Message.fromMap` throws `StateError` if `'sender'` value doesn't match any `Sender` enum name
**File:** `nemo-app/lib/models/message.dart:42`

Fix: Add `orElse: () => Sender.nemo` to the `firstWhere` call.

---

**[BUG-27 / chat]** `_toggleRecording` sends `'[No transcript — add Groq API key]'` as real message content to Nemo
**File:** `nemo-app/lib/screens/chat_screen.dart:162`

Fix: Check `if (result.transcript == null)` before sending. Show a SnackBar and skip `NemoService.send()`.

---

**[N-04 / notifications]** `isAccessGranted()` partial package name match — minor privacy bypass
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/NemoNotificationListenerService.kt:153`

Fix: `flat.split(':').any { it.startsWith(ctx.packageName + '/') }`

---

**[N-05 / notifications]** Notification buffer stored in unencrypted SharedPreferences on Kotlin side
**File:** `nemo-app/android/app/src/main/kotlin/com/avazbek/nemo_app/NemoNotificationListenerService.kt:94`

Fix: Replace with `EncryptedSharedPreferences` from `androidx.security.crypto`.

---

**[V-05 / vision]** `_close()` calls `maybePop` but idle Timer fires even after dispose
**File:** `nemo-app/lib/screens/vision_mode_screen.dart:133`

Fix: In `_resetIdle()`, guard: `if (!mounted) return;` before creating the timer.

---

**[BUG-03 / update]** `IOClient` leaked in `openDownloadInBrowser()` happy path
**File:** `nemo-app/lib/services/update_service.dart:211`

Fix: Wrap in `try/finally: final client = IOClient(...); try { ... } finally { client.close(); }`

---

**[BUG-05 / version]** Version comparison: `int.tryParse()` returns 0 on non-numeric `buildNumber` — infinite update prompts
**File:** `nemo-app/lib/services/update_service.dart:66`

Fix: Add a guard: `if (installed == 0 && _serverVersion > 0) skip` — don't auto-prompt when local version can't be read.

---

**[BUG-06 / update]** `downloadAndInstall()` does not delete partial APK file on installer failure
**File:** `nemo-app/lib/services/update_service.dart:140`

Fix: Delete the file in a `finally` block or on exception after `_download()`.

---

**[BUG-17 / update]** `openDownloadInBrowser()` sets `_updateAvailable=false` without confirming browser opened
**File:** `nemo-app/lib/services/update_service.dart:193`

Fix: Do not set `_updateAvailable=false` in `openDownloadInBrowser()`. Set a separate `_browserFallbackUsed` flag instead.

---

**[BUG-21 / chat_list]** `_maybePromptUpdate` called inside `Consumer` builder — side effects in build
**File:** `nemo-app/lib/screens/chat_list_screen.dart:181`

Fix: Move `_maybePromptUpdate()` to `didChangeDependencies` or a listener registered in `initState`.

---

**[BUG-22 / device_id]** `NemoService` generates a new `device_id` on every `configure()` call
**File:** `nemo-app/lib/services/nemo_service.dart:50`

Fix: Store `device_id` in `FlutterSecureStorage`. Read it once and reuse, identical to `VoiceChatService` approach.

---

## Flow-Breaking Issues

These issues break the user experience end-to-end, causing silent failures or incorrect states with no recovery path.

---

**[F-02 / mute]** Long Nemo reply (>15 s) gets server-side truncation — reply cuts off mid-sentence

**Trigger:** Nemo speaks for more than 15 seconds continuously.
**What breaks:** At t=15s, the mute watchdog fires, the mic reopens, server VAD detects echo, and interrupts the turn server-side. The user hears Nemo abruptly stop mid-sentence with no apparent reason.
**Fix:** Change the mute watchdog to match or exceed `_maxMuteMs` (30 s). Remove the service-layer watchdog; rely solely on the controller's `_unmuteTimer` with gen-based invalidation.

---

**[FLOW-01 / settings]** Settings save does not reconnect — user is stuck on old server forever

**Trigger:** User changes Server URL or App Token in Settings, taps "Save & Reconnect".
**What breaks:** The snackbar says "Saving — reconnecting…" but `configure()` only updates fields. The old WebSocket to the old server with the old token remains open indefinitely.
**Fix:** After `configure()`, call `nemo.disconnect()` followed by `nemo.connect()`.

---

**[FLOW-02 / pairing]** Pairing with wrong credentials permanently sets `isPaired=true`

**Trigger:** First launch, user enters a reachable URL but wrong token, taps Connect.
**What breaks:** Credentials are written to storage before any connection attempt. On every relaunch, the app silently retries with permanently wrong credentials. No way back to the pairing screen without clearing app data.
**Fix:** Validate credentials before storage. Navigate to `ChatListScreen` only on successful authentication.

---

**[FLOW-01 / voice]** WebSocket drops mid-send → reconnect → thinking spinner stuck forever

**Trigger:** User sends message. WS drops before server replies.
**What breaks:** `ChatScreen._thinking` stays true (set in `_send`) and is never cleared because no `'message'` reply arrives.
**Fix:** On disconnect while `state==thinking`, emit an error event so `chat_screen` can clear `_thinking` and show a retry prompt.

---

**[F-03 / voice action]** Voice action + end session: action result not delivered after session ends

**Trigger:** User says "send a Telegram to Aziz saying see you at 8" then immediately says "shut up".
**What breaks:** The tg_msg action takes 7+ seconds. The session ends while it runs. When it finishes, `_voice.sendActionResult()` hits `_ws=null` and silently discards the result. User does not know if the action worked and may trigger a duplicate.
**Fix:** Buffer pending action results for delivery on the next session, or log the result to the transcript regardless of WS state.

---

**[FLOW-03 / tls]** Meeting recording upload over cleartext HTTP when server URL is `ws://`

**Trigger:** User pairs with `ws://` URL; starts and stops a meeting recording.
**What breaks:** `_uploadRecording()` converts `ws://` to `http://`, sending the bearer token and entire audio file over cleartext HTTP. No error shown to the user.
**Fix:** Enforce `wss://` at pairing. Always derive `https://` from `wss://`, never `http://` from `ws://`.

---

**[FLOW-02 / ota]** Voice session reconnect after token expiry/change — stale in-memory token reused

**Trigger:** App token rotated on the server while `VoiceChatService` is in auto-reconnect mode.
**What breaks:** Reconnect attempts fail with auth rejection every 500ms–8s for up to 6 attempts, then emit a generic "Voice connection lost" message. No indication that re-pairing is needed.
**Fix:** Distinguish auth-failure WS close codes (4001 = unauthorized) from network errors. On auth rejection, stop reconnecting and emit: "Authentication failed — re-pair in Settings".

---

**[FLOW-04 / ota]** OTA install with browser fallback clears update banner even if user cancels browser

**Trigger:** In-app install fails → user picks "Use browser" → cancels the chooser.
**What breaks:** `_updateAvailable=false` is set unconditionally after `invokeMethod('openUrl')`. The update banner disappears even though the old APK is still installed.
**Fix:** Do not set `_updateAvailable=false` in `openDownloadInBrowser()`. Only clear it after `_markInstalled` confirms the new build.

---

**[FLOW-04 / voice timeout]** Voice session idle timeout (120 s) fires while Nemo is still speaking a long reply

**Trigger:** User asks a complex question. Nemo takes 120+ seconds to answer.
**What breaks:** The idle timer started after the user's last transcript fires before `turn_complete`. `stop()` is called, the voice session closes mid-sentence. Nemo's voice cuts out. User hears nothing.
**Fix:** In `_onControl('agent_audio_start')`, cancel `_idleTimer` without scheduling a new one. Only restart the idle timer after the unmute timer fires.

---

**[F-04 / vision + voice]** Vision mode + idle timer: idle timer kills voice session while camera is open

**Trigger:** User is examining something through the camera with voice session active but not speaking for 120 s.
**What breaks:** `_idleClose()` fires, ends the voice session, re-arms wake word. User cannot ask "what is this?" because voice pipeline is dead but camera is still open.
**Fix:** Pause the idle timer while vision mode is open (`VisionMode.isOpen` check in `_resetIdle()`).

---

**[FLOW-05 / wake_word]** Wake word fires during voice session — starts a second nested session

**Trigger:** Voice session active → user says "hey nemo" loudly.
**What breaks:** The `_busy` guard in `start()` should prevent this — but if the `_running=false` set in `stop()` (BUG-24 race) happens before native stop, `onWakeWord` fires after `_running=false` with `_busy=false`, starting a second session.
**Fix:** Fix BUG-24 so `_running=false` is only cleared after native stop confirms. Additionally check `_wake.isActive` in `VoiceSessionController.start()`.

---

**[F-01 / vision]** Camera already in use by another app — voice grab silently returns null

**Trigger:** User opens Instagram (which holds the camera), then says "hey nemo what do you see".
**What breaks:** `_init()` throws `CameraException` (camera in use). Caught silently. Returns a raw exception string to the voice agent.
**Fix:** Detect `CameraException.description == 'cameraInUse'` specifically and return a human-readable error piped through the voice agent.

---

**[FLOW-01 / audio]** TOFU pin reset + concurrent WS connect = permanent wrong-cert pin

**Trigger:** User changes Server URL in Settings while a background reconnect timer fires simultaneously.
**What breaks:** Both the reconnect and the new connection enter the TOFU path concurrently. The last writer's observed cert wins, potentially pinning a MITM cert permanently.
**Fix:** Cancel all active connections before `resetPin()` returns. Serialize the reset+reconnect sequence.

---

**[F-03 / background + lifecycle]** Backgrounding during `_init()` leaves camera live — screen closes but hardware not released

**Trigger:** Say "hey nemo what do you see", then immediately press home before camera finishes initializing.
**What breaks:** `didChangeAppLifecycleState(inactive)` fires; `_controller` is still null so the dispose is a no-op. `_init()` resumes, assigns the controller, and starts the idle timer. Camera hardware is live while app is backgrounded. Camera LED on.
**Fix:** See V-03 fix above (`_disposed` flag).

---

**[BUG-09 / recording upload]** Reconnect during meeting recording resets session context — recording upload uses orphaned ID

**Trigger:** Network drops during a meeting recording; reconnect succeeds.
**What breaks:** New server session has no knowledge of the ongoing recording. When `record_stop` arrives (or session ends), the upload uses the original `_recId` which the new session cannot correlate.
**Fix:** On reconnect, re-send a `'recording_resume'` message with `_recId`. Or finalize and upload the recording at disconnect (treat reconnect as a session boundary for recordings).

---

**[FLOW-02 / voice]** Proactive TTS fires while voice session is active — double audio

**Trigger:** Scheduled server-side reminder fires while user is in a live voice session.
**What breaks:** `main.dart`'s `audioB64` listener calls `voice.playAudio(b64)` (just_audio MP3) while `NativeVoicePlayer` streams Nemo's live voice PCM (AudioTrack). Both play at full volume simultaneously.
**Fix:** In the `audioB64` listener, check `if (voiceSession.isActive) return;`.

---

**[BUG-11 / proactive]** `post_proactive` fires unconditionally even when voice session is active — reminder delivered twice

**Trigger:** `VOICE_PROACTIVE=1`, a reminder fires while a voice session is active.
**What breaks:** `post_proactive` injects into the voice session (Qwen speaks it) AND `engine.submit()` produces Edge TTS audio via `NemoService.audioB64`. User hears the reminder twice, possibly overlapping.
**Fix:** When `VOICE_PROACTIVE=1` AND `post_proactive` returns 202 (active session, injection succeeded), skip `engine.submit()` for that reminder.

---

**[FLOW-03 / chat]** User sends image → navigates back before reply → reply stored in wrong session context

**Trigger:** User sends an image then immediately taps back. `ChatScreen` is disposed, `_msgSub` cancelled.
**What breaks:** The reply arrives on `nemo.messages` but `_msgSub` is already cancelled. The message is never saved. User sees a one-sided conversation.
**Fix:** Save incoming Nemo replies at the `NemoService` layer regardless of the screen being alive.

---

**[F-05 / recording]** Meeting recording upload fails mid-session — no retry, file silently orphaned

**Trigger:** Server unreachable during recording upload.
**What breaks:** `_uploadRecording()` catches the exception and returns. No retry, no user notification. The WAV file accumulates on device storage silently. Transcript never appears in Nemo's memory.
**Fix:** Add the file to a retry queue persisted in SharedPreferences. Attempt retry on next launch or connectivity return. Surface failure to the user.

---

**[FLOW-04 / barge-in]** Reconnect while `_idleClose` timer pending — reconnected signal resets `nemoSpeaking=false` while audio already playing

**Trigger:** Network blip during Nemo speech → reconnect → server continues reply → `agent_audio_start` arrives before `reconnected` control signal.
**What breaks:** `'reconnected'` resets `nemoSpeaking=false` and opens the mic while Nemo is actively speaking on the new session → echo barge-in.
**Fix:** In `_onControl('reconnected')`, only reset `nemoSpeaking=false` if it was `false` before reconnect. Save the pre-reconnect value.

---

## Security Vulnerabilities

### Threat Model

The Nemo APK has full phone control capabilities (accessibility service, notification listener, screenshot via MediaProjection, camera, text injection). A compromised or stolen app token grants an attacker silent, unconfirmed control over the entire device. The following vulnerabilities create pathways to token theft, authentication bypass, and unauthorized command execution.

---

**Authentication & Authorization**

| ID | Severity | Issue |
|---|---|---|
| BUG-003 | CRITICAL | Biometric gate permanently disabled — all phone commands execute without confirmation |
| BUG-001 | CRITICAL | `VOICE_INTERNAL_TOKEN` unset = proactive injection endpoint open |
| BUG-002 | CRITICAL | App token in plaintext multipart body during recording upload |
| BUG-008 | HIGH | `device_id` generated from timestamp — predictable, enumerable |
| BUG-009 | HIGH | `device_id` in WebSocket URL query parameter — leaks to access logs |
| BUG-010 | HIGH | TLS downgrade to cleartext HTTP when server URL uses `ws://` |
| ARCH-004 | HIGH | Single shared app token — no per-capability scoping, no token expiry |

---

**TLS / Certificate Pinning**

| ID | Severity | Issue |
|---|---|---|
| BUG-005 | HIGH | `SecureNet._loaded` async race — concurrent callers both enter TOFU path |
| BUG-011 | HIGH | `ssl.CERT_NONE` for loopback engine kick — no cert verification |
| ARCH-002 | HIGH | TOFU window is real — `resetPin()` creates a MITM window during URL changes |
| BUG-004 | HIGH | `debugPrint` leaks full server cert fingerprint to `adb logcat` |
| BUG-012 | MEDIUM | Non-constant-time APK SHA-256 comparison — timing oracle |
| FLOW-003 | HIGH | Meeting recording upload over cleartext HTTP on `ws://` server URL |

---

**Prompt Injection**

| ID | Severity | Issue |
|---|---|---|
| BUG-006 | HIGH | `delegate_task` sanitization incomplete — Markdown/HTML/Unicode not stripped |
| BUG-007 | HIGH | `/internal/proactive` injects reminder text without sanitization |
| ARCH-003 | HIGH | Full voice pipeline: mic → transcription → phone command with no confirmation layer |

---

**Sensitive Data Storage & Logging**

| ID | Severity | Issue |
|---|---|---|
| BUG-016 | MEDIUM | `FlutterSecureStorage` without hardware-backed key — extractable on rooted devices |
| A-03 | MEDIUM | Notification buffer in unencrypted Kotlin SharedPreferences |
| BUG-013 | MEDIUM | Meeting recording WAV persists on disk after upload |
| BUG-014 | MEDIUM | Downloaded APK not deleted from temp directory |
| BUG-018 | LOW | Voice function call arguments (including personal data) logged at INFO level |
| BUG-019 | LOW | Memory notes logged at INFO level |
| N-05 | LOW | Notification buffer stored in unencrypted SharedPreferences |

---

**OTA Security**

| ID | Severity | Issue |
|---|---|---|
| ARCH-001 | HIGH | SHA-256-only APK verification — no Android signing certificate pin |
| B21 | MEDIUM | `RELEASE_CERT_SHA256` is 63 chars (should be 64) — OTA install always rejected |

---

## Architectural Weaknesses

---

**[A-01]** `_busy` guard covers only `start()`, leaving `stop()` unguarded — half a mutex

Every rapid user interaction (double-tap orb, immediate wake-word after manual stop) can cause concurrent start/stop, corrupting session state and closing valid sockets.

**Proposed fix:** Use an explicit state enum: `IDLE | STARTING | ACTIVE | STOPPING`. All transitions are mutually exclusive.

---

**[A-02]** Meeting recording upload is a blocking synchronous part of `stop()` — `stop()` can be 5 minutes long

`VoiceChatService.stop()` awaits `_stopMeetingRecordingAndUpload()` which has a 5-minute HTTP timeout. Wake word is not re-armed during this window. Audio plays while the UI shows "inactive".

**Proposed fix:** Close recorder and socket first, then fire upload as a detached background task: `unawaited(_uploadDetached(pcmPath, id, startMs));`

---

**[ARCH-01 / wake_word]** Wake word lives on main isolate but foreground service runs a background isolate — no IPC path to re-arm after kill

After any OOM kill + service restart, the background isolate restarts (keeping the notification alive) but there is zero mechanism to tell the main isolate to re-arm the `WakeWordEngine`. The notification claims "Listening for Hey Nemo" but wake word is dead.

**Proposed fix:** Use `FlutterForegroundTask.sendDataToMain()` from `onStart()` to ping the main isolate, and wire a receive handler in `main.dart` to call `wake.start()` on receipt.

---

**[ARCH-02 / mic handoff]** No OS-level confirmation that mic has been released between wake word and voice session

The 300 ms `Future.delayed` is device-specific. On MIUI/EMUI under load, `WakeWordEngine.release()` can take >300 ms. `AudioRecorder.startStream()` then fails silently with `AudioRecord.ERROR_INVALID_OPERATION`.

**Proposed fix:** Add a `WakeWordController.isReleased()` channel method. Poll it from Dart with a timeout instead of sleeping.

---

**[ARCH-03 / AEC]** `AudioEffects.enable()` attaches AEC to session ID 0 (global output mix) — has no effect on capture session

The Flutter `record` plugin does not expose its `AudioRecord` session ID. AEC on session 0 is a no-op for capture on most Android devices. `kFullDuplex=true` will produce severe echo on any device where the hardware AEC does not engage via audio source routing alone.

**Proposed fix:** Replace the `record` Flutter plugin for the voice path with a native `AudioRecord` that exposes `audioSessionId`. Bind `AcousticEchoCanceler` to that specific session. This is already called out as a TODO in `AudioEffects.kt`.

---

**[A-03 / executor]** `PhoneCommandExecutor` has no command queue — concurrent execution corrupts UI automation flows

Two commands arriving within milliseconds interleave UI gestures across different apps. `tg_msg` + `tap` executing in parallel will corrupt Telegram navigation.

**Proposed fix:** Wrap `PhoneActionService`'s listener in a serial execution queue: a `StreamController` with `asyncExpand` or an explicit mutex/semaphore.

---

**[A-04 / timeouts]** No `MethodChannel` call timeout at any layer — hangs propagate to server indefinitely

One hung camera capture blocks the action slot forever. Server-side goroutines accumulate on every reconnect that replays pending actions.

**Proposed fix:** Add `.timeout(const Duration(seconds: 15))` at the `_dispatch` level. Return `ActionOutcome.fail('command timed out')` on timeout and send that result to the server.

---

**[ARCH-001 / ota]** SHA-256-only APK verification — no Android signing certificate pin

A successful TLS MITM during the TOFU window can deliver a malicious APK that passes SHA-256 verification but carries a different signing key, successfully installing if no previous version exists.

**Proposed fix:** Embed the expected APK signing certificate fingerprint as a compile-time constant. After SHA-256 verification, extract and compare the APK's embedded signature using `PackageManager.getPackageArchiveInfo()`.

---

**[ARCH-002 / tofu]** TOFU window is real — `resetPin()` creates a real MITM window

`resetPin()` sets `_tofuOverride=true` immediately. Any concurrent connection attempt runs with `_pin=null` and `_tofuOverride=true`, entering the TOFU path. A network-adjacent attacker can permanently pin their own certificate during the brief window.

**Proposed fix:** Cancel all active connections before `resetPin()` returns. Require the user to provide the new server's expected cert fingerprint (e.g. from a QR code) rather than falling back to TOFU.

---

**[ARCH-003 / voice injection]** Voice injection pipeline has no end-to-end trust boundary between transcription and phone command execution

Audio → Deepgram/Qwen → voice_brain → ActionBridge → phone command. The STT output is treated as fully trusted user intent. A physical proximity attacker can speak commands that execute without confirmation.

**Proposed fix:** Implement a command confirmation step for destructive/invasive commands. Before executing `tap/swipe/type/tg_msg/camera`, the agent should speak "should I [action]?" and wait for a yes/no transcript.

---

**[ARCH-004 / token]** Single shared app token — no per-capability scoping, no token expiry

A leaked token grants permanent full access to all endpoints: WebSocket auth, update download, recording upload, phone control, memory read/write.

**Proposed fix:** Implement capability-scoped tokens: long-lived pairing token for re-authentication only, short-lived session tokens for WS sessions, one-time tokens for upload (analogous to `dltoken`). Implement `/rekey` endpoint.

---

**[ARCH-01 / audio]** Two parallel audio delivery paths for proactive content with no arbitration

Proactive spoken content can arrive via `NemoService.audioB64 → VoiceService.playAudio` (Edge TTS MP3) and `voice server /internal/proactive → Qwen synthesizes speech → NativeVoicePlayer` simultaneously. Users hear reminders spoken twice.

**Proposed fix:** When `VOICE_PROACTIVE=1` AND a voice session is active, use ONLY the voice session injection path. Skip `engine.submit()` when `post_proactive` returns 202.

---

**[ARCH-02 / audio]** `VoiceService` (Edge TTS player) and `NativeVoicePlayer` are architecturally unaware of each other

No `AudioFocus` coordination between them at the Flutter layer. `just_audio`'s `AudioPlayer` responds to focus loss by pausing; neither path knows the other exists.

**Proposed fix:** `VoiceService.playAudio()` should check if a voice session is active and skip playback if so. Long-term: consolidate to a single `AudioOutputManager` that arbitrates between proactive TTS and voice session PCM.

---

**[ARCH-01 / nemo_service]** `NemoService.configure()` is stateless — does not tear down existing connection

`configure()` is a pure field-setter. Both call sites assume it's sufficient to switch servers. The actual WS lifecycle is left entirely to the caller, but `settings_screen` does not call `disconnect()/connect()`.

**Proposed fix:**
```dart
void reconfigure(String serverUrl, String token) {
  if (_serverUrl == serverUrl && _token == token) return;
  _serverUrl = serverUrl;
  _token = token;
  disconnect();
  connect();
}
```

---

**[ARCH-03 / chat]** Thinking state is owned by both `NemoService` and `ChatScreen` — they can diverge

`NemoState.thinking` (in `NemoService`) and `_thinking` (local bool in `ChatScreen`) can get out of sync. If `NemoService` transitions back via an audio-only reply, `chat_screen._thinking` stays true indefinitely.

**Proposed fix:** Remove local `_thinking` from `ChatScreen` entirely. Derive it from `context.watch<NemoService>().state == NemoState.thinking`.

---

**[ARCH-04 / chat_storage]** `ChatStorage` schema version hardcoded to 1 with no `onUpgrade` — schema changes will silently use old schema

**Proposed fix:** Add `onUpgrade: (db, oldVersion, newVersion) { ... }` handler even if empty now. Add `ChatStorage.close()` that sets `_db=null`.

---

## Proposed Strong Architecture

### SP-1: Replace raw ChangeNotifiers with Riverpod AsyncNotifier for `NemoService`

```dart
// providers/nemo_providers.dart
@Riverpod(keepAlive: true)
class NemoConnection extends _$NemoConnection {
  NemoSocket? _socket;

  @override
  Future<NemoSocket> build() async {
    final creds = await ref.watch(nemoCredentialsProvider.future);
    if (!creds.isPaired) throw const NemoNotPairedException();
    _socket = await NemoSocket.connect(creds);
    ref.onDispose(() => _socket?.close());
    return _socket!;
  }
}

@riverpod
Stream<NemoMessage> nemoMessages(Ref ref) async* {
  final socket = await ref.watch(nemoConnectionProvider.future);
  yield* socket.messages;
}
```

Benefits: typed async states, auto-dispose, no construction ordering bugs, selective rebuilds.

---

### SP-2: Sealed-class state machine for `VoiceSessionController`

```dart
sealed class VoiceState { const VoiceState(); }
final class Idle extends VoiceState { const Idle(); }
final class Listening extends VoiceState {
  const Listening({required this.sessionToken});
  final Object sessionToken;
}
final class NemoSpeaking extends VoiceState {
  const NemoSpeaking({required this.sessionToken, required this.estPlaybackEndMs});
  final Object sessionToken;
  final int estPlaybackEndMs;
}

void _onControl(String signal, VoiceState current) {
  switch ((signal, current)) {
    case ('agent_audio_start', Listening(:final sessionToken)):
      state = NemoSpeaking(sessionToken: sessionToken,
                           estPlaybackEndMs: DateTime.now().millisecondsSinceEpoch);
    case ('turn_complete', NemoSpeaking(:final sessionToken, :final estPlaybackEndMs)):
      _scheduleUnmute(sessionToken, estPlaybackEndMs);
    case ('interrupted', NemoSpeaking(:final sessionToken)):
      state = Listening(sessionToken: sessionToken);
      _player.flush();
    case _:
      debugPrint('unexpected: $signal in $current');
  }
}
```

Benefits: eliminates `_gen` integer hack, invalid state combos are unrepresentable, compiler flags missing transitions.

---

### SP-3: Runtime-configurable `VoiceOptions` (replaces `const bool kFullDuplex`)

```dart
class VoiceOptions extends ChangeNotifier {
  bool _fullDuplex = false;
  bool get fullDuplex => _fullDuplex;

  Future<void> load() async {
    _fullDuplex = (await _storage.read(key: 'full_duplex')) == 'true';
    notifyListeners();
  }

  Future<void> setFullDuplex(bool on) async {
    _fullDuplex = on;
    await _storage.write(key: 'full_duplex', value: on ? 'true' : 'false');
    notifyListeners();
  }

  Future<void> demoteToHalfDuplex() => setFullDuplex(false);
}
```

Benefits: A/B testing without redeployment, server-negotiated per-device mode, `fullduplex_demote` can permanently adjust stored preference.

---

### SP-4: `NavigationService` (replaces `GlobalKey<NavigatorState>`)

```dart
sealed class NavRequest {}
final class ShowBiometricRequest extends NavRequest {
  final String reason;
  final Completer<bool> result;
  ShowBiometricRequest(this.reason) : result = Completer();
}

class NavigationService {
  final _controller = StreamController<NavRequest>.broadcast();
  Stream<NavRequest> get requests => _controller.stream;

  Future<bool> requestBiometric(String reason) {
    final req = ShowBiometricRequest(reason);
    _controller.add(req);
    return req.result.future.timeout(const Duration(seconds: 30),
                                     onTimeout: () => false);
  }
}
```

Benefits: no null `currentContext` race, testable without a widget tree, hot-reload safe, screen-aware routing.

---

### SP-5: Command registry pattern (replaces 110-line switch in `PhoneCommandExecutor`)

```dart
abstract interface class CommandHandler {
  String get verb;
  Future<ActionOutcome> execute(List<String> args, {BuildContext? context});
}

class PhoneCommandExecutor {
  final _handlers = <String, CommandHandler>{};

  PhoneCommandExecutor() {
    _register(AlarmHandler());
    _register(ScreenshotHandler());
    _register(TelegramMsgHandler());
    // each is a separate file, ~30-40 lines
  }

  void _register(CommandHandler h) => _handlers[h.verb] = h;

  Future<ActionOutcome> execute(String cmd, {BuildContext? context}) async {
    final parts = cmd.trim().split(' ');
    final handler = _handlers[parts[0].toLowerCase()];
    if (handler == null) return ActionOutcome.fail('unknown command: ${parts[0]}');
    if (BiometricService.isSensitive(parts[0])) {
      if (context == null || !context.mounted)
        return const ActionOutcome.fail('locked — open Nemo to confirm');
      if (!await BiometricService.authenticate(context, 'Nemo wants to: $cmd'))
        return const ActionOutcome.fail('user denied');
    }
    return await handler.execute(parts.skip(1).toList(), context: context);
  }
}
```

Benefits: open/closed principle, each handler independently testable, biometric gating as a decorator.

---

### SP-6: `WsConnection` base class (unifies duplicate reconnect logic in `NemoService` and `VoiceChatService`)

```dart
class WsConnection {
  WsConnection({required this.uri, required this.authPayload,
                required this.onMessage, required this.onReconnecting,
                required this.onReconnected, required this.onGaveUp,
                this.maxReconnects = 6});

  final Uri uri;
  final Map<String, dynamic> authPayload;
  final MessageHandler onMessage;
  // ...

  Future<bool> _open() async {
    final client = await SecureNet.httpClient();
    final ch = IOWebSocketChannel.connect(uri, customClient: client);
    await ch.ready.timeout(const Duration(seconds: 8));
    ch.sink.add(jsonEncode(authPayload));
    _sub = ch.stream.listen(_handleMessage, onDone: _onDrop, onError: (_) => _onDrop());
    return true;
  }

  void _onDrop() {
    if (_stopping) return;
    if (_attempts >= maxReconnects) { onGaveUp(); return; }
    onReconnecting();
    final ms = (500 * (1 << _attempts)).clamp(500, 8000);
    _attempts++;
    Timer(Duration(milliseconds: ms), () async {
      if (_stopping) return;
      final ok = await _open();
      ok ? ((_attempts = 0), onReconnected()) : _onDrop();
    });
  }
}
```

Benefits: one place for backoff tuning, `SecureNet.httpClient()` called once per connection, auth handshake bug fixed for both backends simultaneously.

---

## End-to-End Flow Diagrams (ASCII)

### Voice Session Flow

```
User says "hey nemo"
        |
        v
WakeWordEngine (native AudioRecord)
  - score >= 0.3 threshold
  - 3s debounce
        |
        v
WakeWordService._onNative()
  - _running check
  - onWakeWord callback
        |
        v
VoiceSessionController.start()
  - _busy guard (prevents concurrent start)
  - wake.stop() → 300ms delay (mic handoff)
  - _player.start() (AudioTrack PLAYING state)
  - _voice.start() (VoiceChatService)
        |
        v
VoiceChatService.start()
  - AudioSession.setActive(true)
  - AudioRecorder.startStream() → PCM chunks → Deepgram WS
  - WebSocket connect → auth message → server 'ready'
        |
        v
User speaks
        |
        v
Deepgram transcription → server voice_brain
        |
        v
Qwen generates response
        |
        v
Server sends frames:
  agent_audio_start → [audio chunks] → turn_complete
        |
        v
VoiceChatService processes frames:
  'agent_audio_start':
    - VoiceSessionController: nemoSpeaking=true, setMuted(true)
    - _voice.setMuted(true): mic muted
  'audio':
    - _audioOut.add(base64 chunk)
    - VoiceSessionController: NativeVoicePlayer.write(pcmBytes)
    - _estPlaybackEndMs updated
  'turn_complete':
    - VoiceSessionController: arms unmute Timer
    - Timer fires: setMuted(false), nemoSpeaking=false
        |
        v
User speaks again (barge-in) or session idles 120s
        |
   [barge-in]                [idle timeout]
        |                          |
'interrupted' signal         _idleClose()
        |                    set idleClosed=true
setMuted(false)              await stop()
_player.flush()              wake.start()
        |                          |
        v                          v
Back to listening            WakeWordEngine re-armed
```

---

### Phone Action Flow

```
Voice agent decides to execute phone action
        |
        v
Server sends 'action' frame over voice WS:
  {type:'action', id:'vcs-uuid', command:'tg_msg John|hello'}
        |
        v
VoiceSessionController._onAction()
  - clears NemoState.thinking
  - awaits PhoneCommandExecutor.execute(cmd, context)
        |
        v
PhoneCommandExecutor._dispatch()
  - BiometricService.isSensitive(verb)? → authenticate or skip
  - _telegramMessage('John', 'hello')
        |
        v
_telegramMessage():
  1. openAppByName('Telegram') → IntentActions.kt → startActivity
  2. await 2500ms (Telegram cold start)
  3. typeText('John') → NemoAccessibilityService.typeText()
     - findFocusedInput() → AccessibilityNodeInfo
     - node.performAction(ACTION_SET_TEXT)
  4. await 800ms
  5. clickFirstResult() → findFirstClickable() → node.performAction(ACTION_CLICK)
  6. await 1600ms (chat opens)
  7. typeText('hello') → NemoAccessibilityService.typeText()
  8. await 400ms
  9. _click('Send') || _click('Send message')
  10. await 500ms
  11. _returnTo(priorPackage) if not Telegram
        |
        v
ActionOutcome.success('message sent to John')
        |
        v
VoiceSessionController._voice.sendActionResult(id, ok:true, result:'...')
        |
        v
Voice server receives action_result → Qwen responds
```

---

### OTA Update Flow

```
App launch (paired)
        |
        v
main.dart: Timer(5s) → UpdateService.checkForUpdate(serverUrl)
        |
        v
SecureNet.httpClient() → GET /apk/version
  Response: {version: 59, sha256: '...64 chars...'}
        |
        v
_serverVersion (59) > installed (58)?
  Yes → _updateAvailable = true → notifyListeners()
        |
        v
ChatListScreen._updateBanner() shows banner
        |
        v
User taps "Update"
_updatePromptShown = true
        |
        v
UpdateService.downloadAndInstall()
        |
        v
SecureNet.httpClient() → GET /apk/download (stream)
  - Write to getTemporaryDirectory()/apk/Nemo.apk
  - SHA-256 hash accumulation
        |
        v
actual SHA-256 == _serverSha256?
  No  → delete file → throw UpdateException('APK hash mismatch')
  Yes → continue
        |
        v
IntentActions.invokeMethod('installApk', {path: ...})
        |
      [success]              [failure — MIUI blocked]
        |                          |
Android installer launches    throw UpdateException
APK verified by Android       _downloading = false
package signer check          notifyListeners()
New APK replaces old               |
                              openDownloadInBrowser() fallback
                                   |
                              GET /apk/dltoken (one-time token)
                              IntentActions.openUrl(downloadUrl?token=...)
                              Browser opens → user downloads APK manually
```

---

## Fix Priority Order

Fix in this order — each item unblocks or reduces risk for those that follow.

**Tier 0 — Prevent crashes and data loss (fix today)**

1. **B-01 / BUG-03** — `dispose()` StreamController race: add `_disposed` guard on every `_controls.add()` / `_errors.add()` call.
2. **B-02** — Stop→start race: extend `_busy` flag to cover `stop()` as well as `start()`.
3. **B-03** — `_underrunSub` leak: save and cancel the subscription in `dispose()`.
4. **B01 (phones)** — `invokeMethod<bool>` receiving `String`: change to `invokeMethod<String>`.
5. **BUG-03 (biometric)** — Re-add `screenshot`, `camera`, `tg_msg`, `type` to `_sensitiveVerbs`.
6. **BUG-002 (token)** — Move app token from multipart body to `Authorization: Bearer` header in recording upload.

**Tier 1 — Security vulnerabilities (fix this week)**

7. **BUG-001 (voice_http)** — Add `VOICE_INTERNAL_TOKEN` to `_check_required_env()`.
8. **BUG-006 / BUG-007 (prompt injection)** — Apply `_sanitize_chunk()` to `delegate_task` text and `/internal/proactive` body.
9. **BUG-010 (TLS downgrade)** — Enforce `wss://` at pairing; always derive `https://` from `wss://`.
10. **BUG-008 (device_id)** — Replace timestamp-based ID with `Uuid().v4()` stored in secure storage.
11. **BUG-009 (device_id URL)** — Remove `device_id` from WebSocket URL query parameters.
12. **BUG-004 (cert fingerprint log)** — Remove fingerprint values from `debugPrint` calls in `SecureNet`.
13. **BUG-011 (ssl.CERT_NONE)** — Restore SSL verification for loopback engine kick.
14. **BUG-016 (storage)** — Add hardware-backed key options to `FlutterSecureStorage`.
15. **ARCH-001 (ota signing)** — Embed APK signing certificate fingerprint as a compile-time constant; verify before install.

**Tier 2 — Flow-breaking UX issues (fix this sprint)**

16. **FLOW-01 (settings)** — Call `disconnect()/connect()` after `configure()` in `settings_screen._save()`.
17. **FLOW-02 (pairing)** — Validate credentials before writing to storage; navigate only on auth success.
18. **F-02 (mute watchdog)** — Increase mute watchdog from 15 s to match `_maxMuteMs` (30 s).
19. **BUG-001 / FLOW-01 (thinking state)** — Clear `NemoState.thinking` on `'audio'` and `'action'` frames.
20. **BUG-03 / ARCH-01 (wake word after kill)** — Wire `FlutterForegroundTask.sendDataToMain()` to call `wake.start()` on service restart.
21. **A-02 (recording blocks stop)** — Detach meeting recording upload from `stop()` teardown path.
22. **FLOW-04 (idle timeout during long reply)** — Cancel and not reschedule idle timer during `agent_audio_start`.
23. **BUG-001 (proactive double audio)** — Gate `audioB64` listener on `!voiceSession.isActive`.

**Tier 3 — Audio quality and reliability**

24. **B-05 (unmute tail)** — Increase unmute tail from 1100 ms to at least 1300 ms.
25. **B-06 (AEC)** — Replace Flutter `record` plugin with native `AudioRecord` to obtain session ID for AEC binding.
26. **B-07 (socket open during upload)** — Close WebSocket before awaiting upload.
27. **BUG-04 (turn_complete barge-in)** — Set `nemoSpeaking=false` immediately on `turn_complete`; use separate `_inDrainWindow` boolean.
28. **FLOW-01 (action watchdog)** — Send intermediate "working..." audio chunks or increase `_TURN_IDLE_SEC` for action turns.

**Tier 4 — Wake word robustness**

29. **BUG-01 / BUG-02 (wake word races)** — Fix `_running` guard in `_onNative()` and stale callback after `stop()`.
30. **BUG-04 (audio focus during call)** — Register `AudioManager.OnAudioFocusChangeListener`.
31. **BUG-10 (in-call false positive)** — Check `AudioManager.getMode()` before forwarding wake word detection.
32. **ARCH-02 (mic handoff)** — Replace 300 ms sleep with `WakeWordController.isReleased()` polling.

**Tier 5 — Phone command robustness**

33. **B02 (timeouts)** — Add `.timeout(Duration(seconds: 15))` to all `invokeMethod` calls.
34. **B03 (accessibility @Volatile)** — Add `@Volatile` to `NemoAccessibilityService.instance`.
35. **B09 / B10 (manifest)** — Add `<queries>` entries for `ACTION_SET_ALARM` and `QUERY_ALL_PACKAGES` permission.
36. **A01 (fixed delays)** — Replace all `Future.delayed` in tg_msg flow with UI state polling.
37. **A03 (command queue)** — Serialize command execution in `PhoneActionService`.
38. **B21 (cert SHA hex)** — Fix `RELEASE_CERT_SHA256` constant (63 → 64 hex chars).

**Tier 6 — Notifications, vision, recording reliability**

39. **N-01 (getParcelableArray)** — Add API 33 branch using `getParcelableArray(key, Bundle::class.java)`.
40. **N-02 (OTP regex)** — Expand upper bound to `{3,11}`.
41. **N-03 (ANR risk)** — Move `push()` off the main thread; debounce notification bursts.
42. **V-03 (camera background leak)** — Add `_disposed` flag checked after every `await` in `_init()`.
43. **BUG-09 (recording upload race on reconnect)** — Send `recording_resume` message with `_recId` on reconnect.
44. **R-02 (missing fsync)** — Force OS flush after `sink.close()` before reading the PCM file for WAV assembly.
45. **B-09 / BUG-013 (WAV files not deleted)** — Delete WAV files in `finally` block after upload (success and failure).

**Tier 7 — Architecture rewrites (planned sprints)**

46. **SP-2** — Sealed-class state machine for `VoiceSessionController` (replaces `_gen` + `_busy` + bool flags).
47. **SP-3** — Runtime `VoiceOptions` ChangeNotifier (replaces `const bool kFullDuplex`).
48. **SP-4** — `NavigationService` (replaces `GlobalKey<NavigatorState>`).
49. **SP-5** — Command registry pattern (splits 343-line `PhoneCommandExecutor`).
50. **SP-6** — `WsConnection` base class (unifies duplicate reconnect logic in `NemoService` and `VoiceChatService`).
51. **SP-1** — Riverpod migration for `NemoService` (long-term; enables proper async state management).
