import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:record/record.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

/// Real-time voice chat via nemo-voice (Gemini Live API).
///
/// Streams 16kHz PCM mic audio to ws://SERVER:3002,
/// receives 24kHz PCM audio back and plays it.
///
/// Protocol:
///   Send: {"type":"audio","data":"<base64_pcm_16kHz_16bit>"}
///   Recv: {"type":"audio","data":"<base64_pcm_24kHz_16bit>"}
///         {"type":"text","data":"transcript"}
///         {"type":"turn_complete"}
class VoiceChatService extends ChangeNotifier {
  WebSocketChannel? _ws;
  AudioRecorder? _recorder;
  StreamSubscription? _recorderSub;
  StreamSubscription? _wsSub;

  bool _active = false;
  bool get isActive => _active;

  // Callbacks for UI
  final StreamController<String> _transcripts = StreamController.broadcast();
  Stream<String> get transcripts => _transcripts.stream;

  final StreamController<Uint8List> _audioOut = StreamController.broadcast();
  Stream<Uint8List> get audioOut => _audioOut.stream;

  Future<void> start(String serverHost) async {
    if (_active) return;

    final uri = Uri.parse('ws://$serverHost:3002');
    _ws = WebSocketChannel.connect(uri);
    await _ws!.ready;

    // Authenticate with same token as main app
    final token = await _storage.read(key: 'app_token') ?? '';
    _ws!.sink.add(jsonEncode({
      'type': 'auth',
      'token': token,
      'user_id': 1965085976,
      'user_name': 'Avazbek',
    }));

    _active = true;
    notifyListeners();

    // Listen for responses from nemo-voice
    _wsSub = _ws!.stream.listen(
      _onMessage,
      onError: (_) => stop(),
      onDone: () => stop(),
    );

    // Start recording and streaming
    _recorder = AudioRecorder();
    final hasPerms = await _recorder!.hasPermission();
    if (!hasPerms) {
      await stop();
      return;
    }

    final stream = await _recorder!.startStream(const RecordConfig(
      encoder: AudioEncoder.pcm16bits,
      sampleRate: 16000,
      numChannels: 1,
    ));

    _recorderSub = stream.listen((chunk) {
      if (_ws != null && chunk.isNotEmpty) {
        final b64 = base64Encode(chunk);
        _ws!.sink.add(jsonEncode({'type': 'audio', 'data': b64}));
      }
    });

    debugPrint('VoiceChatService: started → $uri');
  }

  void _onMessage(dynamic raw) {
    try {
      final data = jsonDecode(raw as String) as Map<String, dynamic>;
      switch (data['type']) {
        case 'audio':
          final b64 = data['data'] as String? ?? '';
          if (b64.isNotEmpty) {
            _audioOut.add(base64Decode(b64));
          }
        case 'text':
          final text = data['data'] as String? ?? '';
          if (text.isNotEmpty) _transcripts.add(text);
        case 'user_transcript':
          final text = data['data'] as String? ?? '';
          if (text.isNotEmpty) _transcripts.add('You: $text');
      }
    } catch (e) {
      debugPrint('VoiceChatService parse error: $e');
    }
  }

  Future<void> stop() async {
    _active = false;
    await _recorderSub?.cancel();
    await _recorder?.stop();
    _recorder?.dispose();
    _recorder = null;
    _recorderSub = null;
    _wsSub?.cancel();
    _ws?.sink.close(ws_status.goingAway);
    _ws = null;
    notifyListeners();
    debugPrint('VoiceChatService: stopped');
  }

  @override
  void dispose() {
    stop();
    _transcripts.close();
    _audioOut.close();
    super.dispose();
  }
}
