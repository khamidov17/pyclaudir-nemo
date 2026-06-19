import 'dart:async';
import 'package:flutter/material.dart';
import 'native_voice_player.dart';
import 'phone_command_executor.dart';
import 'voice_chat_service.dart';
import 'wake_word_service.dart';

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

  void _wire() {
    _voice.transcripts.listen((t) {
      _add(t);
      if (t.startsWith('You:')) _resetIdle();
    });
    // Stream each agent PCM chunk straight to the native player and advance
    // the playback-end clock (24kHz·16-bit mono = 48000 bytes/sec).
    _voice.audioOut.listen((chunk) {
      _player.write(chunk);
      final now = DateTime.now().millisecondsSinceEpoch;
      if (_estPlaybackEndMs < now) _estPlaybackEndMs = now;
      _estPlaybackEndMs += (chunk.length / 48000 * 1000).round();
      _turnChunks++;
      _turnBytes += chunk.length;
    });
    _voice.controls.listen(_onControl);
    _voice.actions.listen(_onAction);
    _voice.errors.listen(_add);
    _player.underruns.listen((n) => _add('⚠ playback gap · underrun #$n'));
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
      _add('▶ speaking · mic closed');
    } else if (signal == 'turn_complete') {
      final secs = (_turnBytes / 48000).toStringAsFixed(1);
      _add('■ turn done · $_turnChunks chunks · ${secs}s audio');
      // Resume the mic only once the buffered audio has finished playing.
      // +1100ms tail: the AudioTrack buffers ~800ms, so audio keeps playing
      // after the last byte we handed it — reopening earlier echoes the tail
      // back in as a phantom barge-in.
      final remaining =
          _estPlaybackEndMs - DateTime.now().millisecondsSinceEpoch + 1100;
      _unmuteTimer?.cancel();
      _unmuteTimer = Timer(
        Duration(milliseconds: remaining.clamp(0, _maxMuteMs)),
        () {
          _voice.setMuted(false);
          nemoSpeaking = false;
          _add('🎤 mic open');
          _resetIdle(); // user's turn — idle clock starts now
        },
      );
    } else if (signal == 'interrupted') {
      // While Nemo is speaking the mic is muted, so a barge-in here is his
      // own voice echoing in — ignore it so it can't chop his sentence.
      if (nemoSpeaking) {
        _add('✋ echo barge-in IGNORED (Nemo speaking)');
        return;
      }
      _add('✋ barge-in → flush');
      _player.flush();
      _estPlaybackEndMs = 0;
      _unmuteTimer?.cancel();
      _voice.setMuted(false);
      _resetIdle();
    } else if (signal == 'reconnecting') {
      // Connection dropped — don't idle-close while we retry; the server
      // restores context on the new session so the talk resumes seamlessly.
      _idleTimer?.cancel();
      _unmuteTimer?.cancel();
      _add('… reconnecting');
    } else if (signal == 'reconnected') {
      // Fresh session is up — clear half-duplex state so the mic is live.
      nemoSpeaking = false;
      _estPlaybackEndMs = 0;
      _player.flush();
      _resetIdle();
      _add('✓ reconnected');
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
    final r = await _executor.execute(
      cmd,
      context: _navigatorKey?.currentContext,
    );
    _voice.sendActionResult(id, ok: r.ok, text: r.text, error: r.error);
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
    _unmuteTimer?.cancel();
    _idleTimer?.cancel();
    nemoSpeaking = false;
    _estPlaybackEndMs = 0;
    await _voice.stop();
    await _player.stop();
    // Hand the mic back to "hey nemo" (no-op if disabled in Settings).
    await _wake.start();
    notifyListeners();
  }

  Future<void> toggle() async => isActive ? stop() : start();

  void _resetIdle() {
    _idleTimer?.cancel();
    if (!_voice.isActive) return;
    _idleTimer = Timer(idleTimeout, _idleClose);
  }

  Future<void> _idleClose() async {
    _add('⏸ no speech ${idleTimeout.inSeconds}s → paused (saves Deepgram cost)');
    await stop();
    idleClosed = true;
    notifyListeners();
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
    _voice.dispose();
    super.dispose();
  }
}
