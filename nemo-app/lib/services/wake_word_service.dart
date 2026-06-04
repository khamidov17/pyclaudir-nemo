import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:speech_to_text/speech_to_text.dart';

/// On-device wake word detection — no API key, fully offline.
///
/// Runs tight 6-second listening windows in a loop so it's always
/// responsive. The Android "ding" is suppressed by the foreground
/// service keeping the mic open continuously.
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

  /// Wake word is OFF by default — the looped STT windows fire Android's
  /// recognizer chime every few seconds. Opt in via Settings.
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
          // Short windows = responsive + fewer dings
          listenFor: const Duration(seconds: 6),
          pauseFor: const Duration(seconds: 4),
          cancelOnError: true,
          partialResults: true,
        );
      } catch (e) {
        debugPrint('WakeWord listen error: $e');
        if (!completer.isCompleted) completer.complete('');
      }

      final result = await completer.future.timeout(
        const Duration(seconds: 8),
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
        // Brief gap between windows to avoid back-to-back dings
        await Future.delayed(const Duration(milliseconds: 800));
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
