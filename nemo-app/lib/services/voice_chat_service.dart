import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:record/record.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

/// Real-time voice chat via nemo-voice (Deepgram Voice Agent).
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

  // Half-duplex: while Nemo is speaking we stop forwarding mic audio so the
  // speaker output isn't captured as the user talking (echo) and so the next
  // user turn starts clean. Set by the screen around playback.
  bool _muted = false;
  void setMuted(bool m) => _muted = m;

  // Callbacks for UI
  final StreamController<String> _transcripts = StreamController.broadcast();
  Stream<String> get transcripts => _transcripts.stream;

  final StreamController<Uint8List> _audioOut = StreamController.broadcast();
  Stream<Uint8List> get audioOut => _audioOut.stream;

  // Control signals from the agent: 'turn_complete' (play buffered audio),
  // 'interrupted' (barge-in — drop buffered audio), 'ready'.
  final StreamController<String> _controls = StreamController.broadcast();
  Stream<String> get controls => _controls.stream;

  final StreamController<String> _errors = StreamController.broadcast();
  Stream<String> get errors => _errors.stream;

  Future<bool> start(String serverHost) async {
    if (_active) return true;

    final uri = _voiceUri(serverHost);
    try {
      _ws = WebSocketChannel.connect(uri);
      await _ws!.ready.timeout(const Duration(seconds: 8));
    } catch (e) {
      _errors.add('Voice server is not reachable. Check nemo-voice service.');
      await stop();
      return false;
    }

    // Authenticate with same token as main app
    final token = await _storage.read(key: 'app_token') ?? '';
    _ws!.sink.add(jsonEncode({
      'type': 'auth',
      'token': token,
    }));

    // Listen for responses from nemo-voice
    _wsSub = _ws!.stream.listen(
      _onMessage,
      onError: (e) {
        _errors.add('Voice connection failed: $e');
        stop();
      },
      onDone: () {
        if (_active) _errors.add('Voice server disconnected.');
        stop();
      },
    );

    // Start recording and streaming
    _recorder = AudioRecorder();
    final hasPerms = await _recorder!.hasPermission();
    if (!hasPerms) {
      _errors.add('Microphone permission is required for voice chat.');
      await stop();
      return false;
    }

    Stream<Uint8List> stream;
    try {
      stream = await _recorder!.startStream(const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: 16000,
        numChannels: 1,
        // Hands-free echo cancellation: without this the mic hears Nemo's
        // own voice from the speaker and Deepgram treats it as the user
        // talking (barge-in), cutting Nemo off — the "interruptive" feel.
        echoCancel: true,
        noiseSuppress: true,
        autoGain: true,
        // voiceCommunication source gives a clean mic + hardware AEC. We do
        // NOT force modeInCommunication/speakerphone — that routed Nemo's
        // playback through the low-quality call path (muffled/fast-sounding).
        // Leaving the global mode normal lets just_audio play through the
        // clear media speaker. Echo is handled by half-duplex muting instead.
        androidConfig: AndroidRecordConfig(
          audioSource: AndroidAudioSource.voiceCommunication,
        ),
      ));
    } catch (e) {
      _errors.add('Could not start microphone stream.');
      await stop();
      return false;
    }

    _active = true;
    notifyListeners();

    _recorderSub = stream.listen((chunk) {
      if (_ws != null && chunk.isNotEmpty && !_muted) {
        final b64 = base64Encode(chunk);
        _ws!.sink.add(jsonEncode({'type': 'audio', 'data': b64}));
      }
    });

    debugPrint('VoiceChatService: started → $uri');
    return true;
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
        case 'turn_complete':
          _controls.add('turn_complete');
        case 'interrupted':
          _controls.add('interrupted');
        case 'ready':
          _controls.add('ready');
        case 'error':
          _errors.add(data['message'] as String? ?? 'Voice error');
      }
    } catch (e) {
      debugPrint('VoiceChatService parse error: $e');
    }
  }

  Uri _voiceUri(String server) {
    final trimmed = server.trim();
    final parsed = Uri.tryParse(trimmed);
    final fromFullUrl = parsed != null && parsed.host.isNotEmpty;
    final scheme = fromFullUrl
        ? (parsed.scheme == 'wss' || parsed.scheme == 'https' ? 'wss' : 'ws')
        : 'ws';
    final host = fromFullUrl ? parsed.host : trimmed.split(':').first;
    final port = fromFullUrl ? (parsed.hasPort ? parsed.port : 3002) : 3002;
    return Uri(scheme: scheme, host: host, port: port);
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
    _controls.close();
    _errors.close();
    super.dispose();
  }
}
