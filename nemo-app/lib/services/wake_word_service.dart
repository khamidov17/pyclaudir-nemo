import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:speech_to_text/speech_to_text.dart';

/// On-device wake word detection — no API key, fully offline.
///
/// Uses long listening windows. Android's built-in speech recognizer can chime
/// whenever listening starts, so short restart loops are intentionally avoided.
class WakeWordService extends ChangeNotifier {
  final SpeechToText _stt = SpeechToText();
  bool _running = false;
  bool _ready = false;
  bool get isActive => _running;
  VoidCallback? onWakeWord;

  static const _triggers = ['nemo', 'hey nemo', 'ok nemo', 'yo nemo'];

  static const _store = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  /// Wake word is OFF by default. Android's SpeechRecognizer plays a system
  /// "ding" and grabs audio focus every time it (re)starts listening, which
  /// pauses/ducks other apps (music, Instagram reels) even when you're not in
  /// a voice chat. Opt in from Settings if you want hands-free "nemo".
  static Future<bool> isEnabled() async =>
      (await _store.read(key: 'wake_word_enabled')) == 'true';

  static Future<void> setEnabled(bool on) async =>
      _store.write(key: 'wake_word_enabled', value: on ? 'true' : 'false');

  Future<void> init() async {
    _ready = await _stt.initialize(
      onError: (e) => debugPrint('wake STT error: ${e.errorMsg}'),
      debugLogging: false,
    );
    debugPrint('WakeWordService ready: $_ready');
  }

  Future<void> start() async {
    if (!_ready || _running) return;
    if (!await isEnabled()) {
      debugPrint('WakeWordService: disabled (opt in via Settings)');
      return;
    }
    _running = true;
    notifyListeners();
    debugPrint('WakeWordService: started');
    _loop();
  }

  Future<void> stop() async {
    _running = false;
    await _stt.cancel();
    notifyListeners();
    debugPrint('WakeWordService: stopped');
  }

  Future<void> _loop() async {
    while (_running) {
      if (_stt.isListening) {
        await Future.delayed(const Duration(milliseconds: 200));
        continue;
      }

      final completer = Completer<String>();

      try {
        await _stt.listen(
          onResult: (r) {
            final text = r.recognizedWords.toLowerCase();
            // Check partial results immediately — no waiting for final
            if (_isTrigger(text) && !completer.isCompleted) {
              completer.complete(text);
            } else if (r.finalResult && !completer.isCompleted) {
              completer.complete('');
            }
          },
          listenOptions: SpeechListenOptions(
            listenFor: const Duration(minutes: 5),
            pauseFor: const Duration(seconds: 30),
            cancelOnError: true,
            partialResults: true,
          ),
        );
      } catch (e) {
        debugPrint('WakeWord listen error: $e');
        if (!completer.isCompleted) completer.complete('');
      }

      final result = await completer.future.timeout(
        const Duration(minutes: 5, seconds: 2),
        onTimeout: () => '',
      );

      if (!_running) break;

      if (_isTrigger(result)) {
        debugPrint('Wake word detected: "$result"');
        await _stt.cancel();
        onWakeWord?.call();
        // Wait for voice interaction to complete before resuming
        await Future.delayed(const Duration(seconds: 8));
      } else {
        await Future.delayed(const Duration(seconds: 2));
      }
    }
  }

  bool _isTrigger(String text) {
    if (text.isEmpty) return false;
    return _triggers.any((t) => text.contains(t));
  }

  @override
  void dispose() {
    _running = false;
    _stt.cancel();
    super.dispose();
  }
}
