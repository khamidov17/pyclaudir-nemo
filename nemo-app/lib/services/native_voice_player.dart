import 'dart:async';
import 'package:flutter/services.dart';

/// Thin wrapper over the native AudioTrack player (Android).
///
/// Plays a continuous 24kHz PCM16 stream on the assistant audio path. Surfaces
/// playback-starvation (underrun) events so the UI can show when the voice
/// goes choppy. See VoicePlayer.kt.
///
/// SINGLETON: the platform channel is process-global, so a per-instance
/// `setMethodCallHandler` would make the last-constructed instance steal the
/// handler and orphan every other one (events into closed controllers).
class NativeVoicePlayer {
  static const _ch = MethodChannel('com.avazbek.nemo_app/voice_player');

  static final NativeVoicePlayer instance = NativeVoicePlayer._();

  final _underruns = StreamController<int>.broadcast();

  /// Emits the running underrun count whenever playback starves (choppy voice).
  Stream<int> get underruns => _underruns.stream;

  NativeVoicePlayer._() {
    _ch.setMethodCallHandler((call) async {
      if (call.method == 'underrun') {
        _underruns.add((call.arguments as int?) ?? 0);
      }
    });
  }

  Future<void> start({int sampleRate = 24000}) =>
      _ch.invokeMethod('start', {'sampleRate': sampleRate});

  /// Queue one PCM16 chunk for immediate playback.
  Future<void> write(Uint8List pcm) => _ch.invokeMethod('write', {'data': pcm});

  /// Barge-in: drop all queued + buffered audio right now.
  Future<void> flush() => _ch.invokeMethod('flush');

  Future<void> stop() => _ch.invokeMethod('stop');
}
