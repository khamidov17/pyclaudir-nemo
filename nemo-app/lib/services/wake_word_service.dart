import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:vosk_flutter_2/vosk_flutter_2.dart';

/// On-device wake word via Vosk — fully offline, NO API key, and crucially NO
/// Android SpeechRecognizer "ding" (Vosk captures raw audio itself, so it never
/// grabs system audio focus or plays the recognizer chime that muted music /
/// Instagram before).
///
/// The small English model (~40MB) is downloaded once on first enable and then
/// cached on device. Recognition is grammar-constrained to the wake phrases for
/// accuracy and low CPU. Everything is wrapped defensively: if Vosk ever fails
/// (no network on first download, model error), wake word simply stays off —
/// the rest of the app is unaffected.
class WakeWordService extends ChangeNotifier {
  static const _modelUrl =
      'https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip';
  static const _triggers = ['nemo', 'hey nemo', 'ok nemo', 'yo nemo'];
  // Constrain recognition to the wake phrases (+ [unk] for everything else).
  static const _grammar = ['hey nemo', 'ok nemo', 'yo nemo', 'nemo', '[unk]'];

  final _vosk = VoskFlutterPlugin.instance();
  SpeechService? _speech;
  StreamSubscription? _partialSub;
  bool _running = false;
  bool _loading = false;
  DateTime _lastFire = DateTime.fromMillisecondsSinceEpoch(0);
  VoidCallback? onWakeWord;
  bool get isActive => _running;

  static const _store = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  /// Wake word is OFF by default (opt in from Settings). Unlike the old engine
  /// it no longer dings, but it still streams the mic, so keep it user-choice.
  static Future<bool> isEnabled() async =>
      (await _store.read(key: 'wake_word_enabled')) == 'true';

  static Future<void> setEnabled(bool on) async =>
      _store.write(key: 'wake_word_enabled', value: on ? 'true' : 'false');

  /// Cheap — the heavy model load is deferred to the first [start] so a user
  /// who never enables wake word never downloads the model.
  Future<void> init() async {}

  Future<void> _ensureSpeech() async {
    if (_speech != null || _loading) return;
    _loading = true;
    try {
      final modelPath = await ModelLoader().loadFromNetwork(_modelUrl);
      final model = await _vosk.createModel(modelPath);
      final recognizer = await _vosk.createRecognizer(
        model: model,
        sampleRate: 16000,
        grammar: _grammar,
      );
      _speech = await _vosk.initSpeechService(recognizer);
      _partialSub = _speech!.onPartial().listen(_onPartial);
    } catch (e) {
      debugPrint('WakeWord vosk init failed (wake word stays off): $e');
    } finally {
      _loading = false;
    }
  }

  Future<void> start() async {
    if (_running) return;
    if (!await isEnabled()) {
      debugPrint('WakeWordService: disabled (opt in via Settings)');
      return;
    }
    await _ensureSpeech();
    if (_speech == null) return;
    try {
      await _speech!.start();
      _running = true;
      notifyListeners();
      debugPrint('WakeWordService: listening (vosk, no ding)');
    } catch (e) {
      debugPrint('WakeWord start failed: $e');
    }
  }

  Future<void> stop() async {
    _running = false;
    try {
      await _speech?.stop();
    } catch (_) {}
    notifyListeners();
    debugPrint('WakeWordService: stopped');
  }

  void _onPartial(String json) {
    String text;
    try {
      text = (jsonDecode(json)['partial'] as String? ?? '').toLowerCase();
    } catch (_) {
      return;
    }
    if (text.isEmpty || !_triggers.any(text.contains)) return;
    // Debounce so a single utterance only fires once.
    final now = DateTime.now();
    if (now.difference(_lastFire) < const Duration(seconds: 4)) return;
    _lastFire = now;
    debugPrint('Wake word detected: "$text"');
    onWakeWord?.call();
  }

  @override
  void dispose() {
    _running = false;
    _partialSub?.cancel();
    _speech?.stop();
    super.dispose();
  }
}
