import 'dart:async';

import 'package:flutter/foundation.dart';

import '../screens/vision_mode_screen.dart';

/// Streams camera frames while server-driven scene narration is active.
///
/// Started on the server's `narration_start` event, stopped on `narration_stop`
/// (or ws close). Grabs a frame from the warm live camera every few seconds and
/// hands it to [onFrame], which sends it as a `narration_frame` on the voice
/// websocket. The server throttles + describes; frames are never journaled.
class SceneStreamer {
  Timer? _timer;
  bool _busy = false;

  bool get active => _timer != null;

  void start(void Function(String imageB64) onFrame) {
    if (_timer != null) return;
    _timer = Timer.periodic(const Duration(seconds: 4), (_) async {
      if (_busy || !VisionMode.isOpen) return;
      _busy = true;
      try {
        final b64 = await VisionMode.grab();
        if (b64 != null) onFrame(b64);
      } catch (e) {
        debugPrint('[narration] frame grab error: $e');
      } finally {
        _busy = false;
      }
    });
    debugPrint('[narration] scene streaming started');
  }

  void stop() {
    _timer?.cancel();
    _timer = null;
    debugPrint('[narration] scene streaming stopped');
  }
}
