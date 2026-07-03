import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// On-device wake word via native **openWakeWord** (ONNX keyword spotter).
///
/// Replaces the old Vosk STT: openWakeWord is a tiny purpose-built model (like
/// "Hey Siri"), far more accurate on the wake phrase and much lighter — and
/// fully offline, with NO network and NO vendor key (works in China). The
/// native engine (WakeWordController.kt) captures the mic itself and reports
/// detections; this service just arms/disarms it and debounces.
///
/// Public interface is unchanged so VoiceSessionController et al. don't change.
/// Ships with a placeholder model until a custom "hey nemo" model is trained
/// (openWakeWord + Colab) and dropped into assets.
class WakeWordService extends ChangeNotifier {
  static const _channel = MethodChannel('com.avazbek.nemo_app/wakeword');
  // Placeholder wake model (assets/hey_jarvis_v0.1.onnx) — so today the wake
  // phrase is "hey jarvis", not "hey nemo". Train a custom hey_nemo.onnx
  // (see scripts/train_wakeword/README.md), drop it in assets/, and change this
  // one constant to 'hey_nemo.onnx' (then re-tune _threshold below).
  static const _model = 'hey_jarvis_v0.1.onnx';
  // The placeholder "hey jarvis" model peaks ~0.39 for this device/voice (the
  // custom "hey nemo" model will score higher). 0.3 sits comfortably between
  // that peak and the ~0.03 quiet floor, so it fires without false-triggering.
  static const _threshold = 0.3;

  bool _running = false;
  DateTime _lastFire = DateTime.fromMillisecondsSinceEpoch(0);
  VoidCallback? onWakeWord;
  bool get isActive => _running;

  static const _store = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  WakeWordService() {
    _channel.setMethodCallHandler(_onNative);
  }

  /// Wake word is OFF by default (opt in from Settings).
  static Future<bool> isEnabled() async =>
      (await _store.read(key: 'wake_word_enabled')) == 'true';

  static Future<void> setEnabled(bool on) async =>
      _store.write(key: 'wake_word_enabled', value: on ? 'true' : 'false');

  /// No-op: the native engine loads its ONNX models lazily on first start().
  Future<void> init() async {}

  Future<dynamic> _onNative(MethodCall call) async {
    if (call.method == 'onWakeWordError') {
      debugPrint('WakeWordService: mic error — ${call.arguments}');
      _running = false;
      notifyListeners();
      return;
    }
    if (call.method != 'onWakeWord') return;
    if (!_running) return; // stop() may have arrived before this native callback
    // Debounce so one utterance fires once (the engine has its own cooldown
    // too, but a short guard here is cheap insurance).
    final now = DateTime.now();
    if (now.difference(_lastFire) < const Duration(seconds: 3)) return;
    _lastFire = now;
    debugPrint('Wake word detected (score=${call.arguments})');
    onWakeWord?.call();
  }

  bool _starting = false;

  Future<void> start() async {
    if (_running || _starting) return;
    _starting = true;
    try {
      await _start();
    } finally {
      _starting = false;
    }
  }

  Future<void> _start() async {
    if (!await isEnabled()) {
      debugPrint('WakeWordService: disabled (opt in via Settings)');
      try {
        await _channel.invokeMethod('toast', {
          'msg': 'Wake word is OFF — enable it in Settings',
        });
      } catch (_) {}
      return;
    }
    try {
      await _channel.invokeMethod('start', {
        'model': _model,
        'threshold': _threshold,
      });
      _running = true;
      notifyListeners();
      debugPrint('WakeWordService: listening (openWakeWord)');
    } catch (e) {
      debugPrint('WakeWord start failed: $e');
    }
  }

  Future<void> stop() async {
    try {
      await _channel.invokeMethod('stop');
    } catch (_) {}
    _running = false;
    notifyListeners();
    debugPrint('WakeWordService: stopped');
  }

  @override
  void dispose() {
    stop(); // unawaited in dispose — best-effort, fire-and-forget
    super.dispose();
  }
}
