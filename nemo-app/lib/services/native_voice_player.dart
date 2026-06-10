import 'package:flutter/services.dart';

/// Thin wrapper over the native AudioTrack player (Android).
///
/// Plays a continuous 24kHz PCM16 stream on the voice-communication path so
/// hardware AEC can cancel Nemo's voice from the mic — enabling full-duplex
/// conversation with barge-in and no mic muting. See VoicePlayer.kt.
class NativeVoicePlayer {
  static const _ch = MethodChannel('com.avazbek.nemo_app/voice_player');

  Future<void> start({int sampleRate = 24000}) =>
      _ch.invokeMethod('start', {'sampleRate': sampleRate});

  /// Queue one PCM16 chunk for immediate playback.
  Future<void> write(Uint8List pcm) => _ch.invokeMethod('write', {'data': pcm});

  /// Barge-in: drop all queued + buffered audio right now.
  Future<void> flush() => _ch.invokeMethod('flush');

  Future<void> stop() => _ch.invokeMethod('stop');
}
