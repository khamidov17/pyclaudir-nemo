import 'dart:async';
import 'package:flutter/material.dart';
import 'native_voice_player.dart';
import 'phone_command_executor.dart';
import 'voice_chat_service.dart';
import 'voice_debug_stats.dart';
import 'wake_word_service.dart';
import '../screens/vision_mode_screen.dart';

export 'voice_debug_stats.dart';

/// App-level owner of the live voice conversation.
///
/// Lives ABOVE the UI: "hey nemo" starts a session from anywhere — while
/// you're in another app or the screen is off — without opening the Nemo UI.
/// Nemo's voice plays over (ducked) whatever you were doing, and phone
/// actions requested by the voice agent run right here. VoiceChatScreen is
/// just a window onto this controller when the user happens to open it.
class VoiceSessionController extends ChangeNotifier {
  VoiceSessionController({
    required WakeWordService wake,
    required String Function() serverUrl,
    required PhoneCommandExecutor executor,
    GlobalKey<NavigatorState>? navigatorKey,
  })  : _wake = wake,
        _serverUrl = serverUrl,
        _executor = executor,
        _navigatorKey = navigatorKey {
    _wire();
  }

  final WakeWordService _wake;
  final String Function() _serverUrl;
  final PhoneCommandExecutor _executor;
  final GlobalKey<NavigatorState>? _navigatorKey;
  final VoiceChatService _voice = VoiceChatService();
  final NativeVoicePlayer _player = NativeVoicePlayer.instance;

  /// Transcript + diagnostics, displayed by VoiceChatScreen.
  final List<String> log = [];
  bool nemoSpeaking = false;
  bool idleClosed = false;
  bool get isActive => _voice.isActive;
  Stream<String> get errors => _voice.errors;

  /// Live debug stats for the debug overlay. Updated during the session.
  VoiceDebugStats debugStats = const VoiceDebugStats(aecEnabled: kFullDuplex);

  // Tracks when a barge-in signal first arrived, for latency measurement.
  int _bargeInStartMs = 0;

  // Estimated wall-clock time (ms since epoch) at which all audio handed to
  // the player so far will have finished playing. Used to resume the mic the
  // instant Nemo actually stops speaking — not before (echo) or much after.
  int _estPlaybackEndMs = 0;
  Timer? _unmuteTimer;
  // The mic can stay closed for as long as Nemo's reply actually plays; this
  // cap only guards against a wildly wrong estimate. It must comfortably
  // exceed the longest real reply — a 6s cap used to reopen the mic mid-reply
  // and the idle timer would then kill the session while Nemo was talking.
  static const _maxMuteMs = 30000;
  // Auto-pause after a LONG silence so a forgotten session eventually releases
  // the mic back to the wake word. Generous (2 min) so normal thinking pauses —
  // or a dropped/late user transcript — never cut the conversation short. The
  // server's own 300s idle timeout is the real ceiling.
  static const idleTimeout = Duration(seconds: 120);
  Timer? _idleTimer;
  int _turnChunks = 0;
  int _turnBytes = 0;
  bool _busy = false;
  bool _nemoSpeakingBeforeReconnect = false;
  StreamSubscription<int>? _underrunSub;
  // Bumped on every session boundary (reconnect/stop). A pending unmute timer
  // captures the generation it was armed under and no-ops if it changed, so a
  // stale timer from a previous session can never reopen the mic.
  int _gen = 0;

  void _wire() {
    _voice.transcripts.listen((t) {
      _add(t);
      if (t.startsWith('You:')) _resetIdle();
    });
    // Stream each agent PCM chunk straight to the native player and advance
    // the playback-end clock (24kHz·16-bit mono = 48000 bytes/sec).
    _voice.audioOut.listen((chunk) {
      // First chunk may arrive before agent_audio_start — mute proactively so
      // Nemo's own voice can't echo back into Deepgram as a phantom barge-in.
      // Guard on isActive (not a captured gen): gen is bumped on stop() so a
      // captured value goes stale after the first reconnect, breaking this guard
      // for every subsequent session. isActive=false drops chunks from a dying
      // session and is always current.
      if (!kFullDuplex && !nemoSpeaking && isActive) {
        _voice.setMuted(true);
        nemoSpeaking = true;
        notifyListeners();
      }
      _player.write(chunk);
      final now = DateTime.now().millisecondsSinceEpoch;
      if (_estPlaybackEndMs < now) _estPlaybackEndMs = now;
      _estPlaybackEndMs += (chunk.length / 48000 * 1000).round();
      _turnChunks++;
      _turnBytes += chunk.length;
    });
    _voice.controls.listen(_onControl,
        onError: (Object e) => _add('voice control error: $e'));
    _voice.actions.listen(_onAction,
        onError: (Object e) => _add('voice action error: $e'));
    _voice.errors.listen(_add);
    _underrunSub = _player.underruns.listen((n) => _add('⚠ playback gap · underrun #$n'));
    _voice.addListener(notifyListeners);
  }

  void _onControl(String signal) {
    if (signal == 'agent_audio_start') {
      // Nemo started talking → close the mic so his voice can't echo back
      // into Deepgram and cut him off. Pause the idle clock while he speaks.
      _unmuteTimer?.cancel();
      _idleTimer?.cancel();
      _voice.setMuted(true);
      _turnChunks = 0;
      _turnBytes = 0;
      nemoSpeaking = true;
      _bargeInStartMs = DateTime.now().millisecondsSinceEpoch;
      _add('▶ speaking · mic closed');
    } else if (signal == 'turn_complete') {
      final secs = (_turnBytes / 48000).toStringAsFixed(1);
      _add('■ turn done · $_turnChunks chunks · ${secs}s audio');
      // Clear speaking immediately so barge-ins during the drain window aren't
      // suppressed. Mic stays muted until the AudioTrack buffer drains.
      nemoSpeaking = false;
      notifyListeners();
      // Resume the mic only once the buffered audio has finished playing.
      // +1300ms tail: AudioTrack buffers ~1200ms (57600 bytes @ 48000 B/s), so
      // audio keeps playing after the last byte — reopening earlier echoes the
      // tail back as a phantom barge-in.
      final remaining =
          _estPlaybackEndMs - DateTime.now().millisecondsSinceEpoch + 1300;
      _unmuteTimer?.cancel();
      final gen = _gen;
      _unmuteTimer = Timer(
        Duration(milliseconds: remaining.clamp(0, _maxMuteMs)),
        () {
          if (gen != _gen) return; // stale — a reconnect/stop happened since
          _voice.setMuted(false);
          _add('🎤 mic open');
          _resetIdle(); // user's turn — idle clock starts now
        },
      );
    } else if (signal == 'interrupted') {
      // Half-duplex: the mic is muted while Nemo speaks, so a barge-in here is
      // his own voice echoing in — ignore it so it can't chop his sentence.
      // Full-duplex: the mic stays open and AEC removes his voice, so a barge-in
      // is a REAL interruption — let it through.
      if (nemoSpeaking && !kFullDuplex) {
        _add('✋ echo barge-in IGNORED (Nemo speaking)');
        return;
      }
      final latencyMs = _bargeInStartMs > 0
          ? DateTime.now().millisecondsSinceEpoch - _bargeInStartMs
          : 0;
      debugStats = debugStats.copyWith(bargeInLatencyMs: latencyMs);
      _bargeInStartMs = 0;
      nemoSpeaking = false;
      _add('✋ barge-in → flush');
      _player.flush();
      _estPlaybackEndMs = 0;
      _unmuteTimer?.cancel();
      _voice.setMuted(false);
      _resetIdle();
    } else if (signal == 'fullduplex_demote') {
      // Server detected self-barge (Nemo's voice leaked through AEC and triggered
      // his own VAD). Demote to half-duplex for the rest of this turn: mute the
      // mic so the echo can't re-trigger, and tally the event for the debug overlay.
      debugStats = debugStats.copyWith(
        selfBargeCount: debugStats.selfBargeCount + 1,
        aecEnabled: false,
      );
      _voice.setMuted(true);
      _add('⚠ fullduplex_demote → half-duplex fallback (self-barge #${debugStats.selfBargeCount})');
    } else if (signal == 'reconnecting') {
      // Connection dropped — new session boundary: bump the generation so any
      // pending unmute timer from the old session can't fire. Don't idle-close
      // while we retry; the server restores context on the new session.
      _nemoSpeakingBeforeReconnect = nemoSpeaking;
      _gen++;
      _idleTimer?.cancel();
      _unmuteTimer?.cancel();
      _add('… reconnecting');
    } else if (signal == 'reconnected') {
      // Only unmute if Nemo wasn't mid-speech before the reconnect. If he was,
      // `agent_audio_start` on the new session will handle muting correctly.
      // Resetting nemoSpeaking=false unconditionally causes echo barge-in when
      // agent_audio_start arrives before this reconnected signal (FLOW-04).
      final wasSpeak = _nemoSpeakingBeforeReconnect;
      _nemoSpeakingBeforeReconnect = false; // consume — must clear before any return
      if (!wasSpeak) {
        nemoSpeaking = false;
        _voice.setMuted(false);
      }
      _estPlaybackEndMs = 0;
      _player.flush();
      _resetIdle();
      _add('✓ reconnected');
    } else if (signal == 'ended') {
      // The voice service gave up after exhausting reconnects. It already tore
      // down its own mic/socket; we must hand the mic back to "hey nemo" or the
      // wake word stays dead until the app restarts.
      unawaited(_onSessionEnded().catchError((Object e) {
        debugPrint('VoiceSessionController: _onSessionEnded error: $e');
      }));
    } else if (signal == 'recording_started') {
      _add('🔴 recording the meeting…');
    } else if (signal == 'recording_stopped') {
      _add('⏹ recording stopped — uploading');
    } else if (signal == 'recording_uploaded') {
      _add('✓ recording uploaded — transcribing');
    } else if (signal == 'deactivate') {
      // User said "shut up / go to sleep" → end the live session NOW and drop to
      // wake-word-only mode. stop() closes the mic/socket (no reconnect) and
      // re-arms the on-device wake word — nothing is streamed until "hey nemo".
      _add('💤 deactivated — say "hey nemo" to wake me');
      idleClosed = true;
      unawaited(stop().catchError((Object e) {
        debugPrint('VoiceSessionController: deactivate stop() error: $e');
      }));
    }
    notifyListeners();
  }

  /// Phone actions requested by the voice agent — execute and answer so Nemo
  /// can confirm out loud. (Images are dropped: the voice model is text-only.)
  Future<void> _onAction(Map<String, dynamic> data) async {
    final id = data['id'] as String? ?? '';
    final cmd = (data['command'] as String? ?? '').trim();
    if (id.isEmpty || cmd.isEmpty) return;
    _add('⚙ $cmd');
    try {
      final r = await _executor.execute(
        cmd,
        context: _navigatorKey?.currentContext,
      );
      _voice.sendActionResult(
          id, ok: r.ok, text: r.text, error: r.error, imageB64: r.imageB64);
    } catch (e) {
      debugPrint('[voice_action] error for $cmd: $e');
      _voice.sendActionResult(id, ok: false, error: e.toString());
    }
  }

  Future<bool> start() async {
    if (_busy) return false;
    if (isActive) return true;
    _busy = true;
    try {
      idleClosed = false;
      // Wake word and voice chat both need the microphone, and Android only
      // grants it to one consumer. Free the mic, give Android a moment to
      // release the input, then open playback before the recorder so the
      // audio session is fully configured when agent audio arrives.
      await _wake.stop();
      await Future.delayed(const Duration(milliseconds: 300));
      await _player.start(sampleRate: 24000);
      final started = await _voice.start(_serverUrl());
      if (started) {
        debugStats = VoiceDebugStats(aecEnabled: kFullDuplex);
        _bargeInStartMs = 0;
        _add('Voice chat started — speak now');
        _resetIdle();
      } else {
        await _player.stop();
        await _wake.start();
      }
      notifyListeners();
      return started;
    } finally {
      _busy = false;
    }
  }

  Future<void> stop() async {
    if (_busy) return;
    _busy = true;
    try {
      _gen++; // invalidate any pending unmute timer
      _unmuteTimer?.cancel();
      _idleTimer?.cancel();
      nemoSpeaking = false;
      _estPlaybackEndMs = 0;
      await _voice.stop();
      await _player.stop();
      // Hand the mic back to "hey nemo" (no-op if disabled in Settings).
      await _wake.start();
      notifyListeners();
    } finally {
      _busy = false;
    }
  }

  Future<void> toggle() async => isActive ? stop() : start();

  /// The voice service self-terminated (reconnects exhausted). Mirror stop()'s
  /// cleanup — but DON'T call _voice.stop() (it already stopped itself) — and
  /// re-arm the wake word so "hey nemo" works again.
  Future<void> _onSessionEnded() async {
    _gen++;
    _unmuteTimer?.cancel();
    _idleTimer?.cancel();
    nemoSpeaking = false;
    _estPlaybackEndMs = 0;
    idleClosed = true;
    await _player.stop();
    await _wake.start();
    notifyListeners();
  }

  void _resetIdle() {
    _idleTimer?.cancel();
    if (!_voice.isActive) return;
    if (VisionMode.isOpen) return; // don't idle-close while camera is in use (F-04)
    _idleTimer = Timer(idleTimeout, _idleClose);
  }

  Future<void> _idleClose() async {
    // Don't close the session while the user is actively using the camera.
    // Re-arm the timer so we close once they're done (F-04/vision+voice).
    if (VisionMode.isOpen) {
      _resetIdle();
      return;
    }
    // start() / toggle() hold _busy across the socket connect (up to 8s).
    // Rather than no-op silently (leaking a live Deepgram session with no timer),
    // retry in 2s — short enough to fire promptly once start() completes.
    if (_busy) {
      _idleTimer = Timer(const Duration(seconds: 2), _idleClose);
      return;
    }
    _add('⏸ no speech ${idleTimeout.inSeconds}s → paused (saves Deepgram cost)');
    idleClosed = true;
    notifyListeners();
    await stop();
  }

  void _add(String s) {
    log.add(s);
    if (log.length > 400) log.removeRange(0, log.length - 400);
    notifyListeners();
  }

  @override
  void dispose() {
    _unmuteTimer?.cancel();
    _idleTimer?.cancel();
    _underrunSub?.cancel();
    _voice.dispose();
    super.dispose();
  }
}
