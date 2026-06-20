import 'dart:async';
import 'dart:convert';
import 'package:audio_session/audio_session.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:record/record.dart';
import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;
import 'secure_net.dart';

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

  // Reconnection: on a transient WS drop (flaky network) we re-open the socket
  // and re-auth WITHOUT tearing down the mic/player — the server restores the
  // conversation context on the new session, so the talk resumes seamlessly.
  String? _host;
  bool _userStopping = false;
  bool _reconnecting = false;
  Timer? _reconnectTimer;
  int _reconnectAttempts = 0;
  static const _maxReconnects = 6;

  // Soft half-duplex: while Nemo is speaking we stop forwarding mic audio to
  // Deepgram so his own voice (echoing from the speaker on the clear media
  // playback path, which has no hardware AEC reference) can't be heard as the
  // user "barging in" and cut his sentence off. The screen toggles this around
  // playback, keyed to when his audio actually finishes. A watchdog guarantees
  // the mic can never stay muted if a resume signal is ever lost.
  bool _muted = false;
  Timer? _muteWatchdog;
  void setMuted(bool m) {
    _muted = m;
    _muteWatchdog?.cancel();
    if (m) {
      _muteWatchdog = Timer(const Duration(seconds: 15), () => _muted = false);
    }
  }

  // Callbacks for UI
  final StreamController<String> _transcripts = StreamController.broadcast();
  Stream<String> get transcripts => _transcripts.stream;

  final StreamController<Uint8List> _audioOut =
      StreamController.broadcast(sync: true);
  Stream<Uint8List> get audioOut => _audioOut.stream;

  // Control signals from the agent: 'agent_audio_start' (mute mic),
  // 'turn_complete' (Nemo finished — resume mic), 'interrupted' (barge-in),
  // 'ready'. The screen acts on these to drive half-duplex + playback.
  final StreamController<String> _controls =
      StreamController.broadcast(sync: true);
  Stream<String> get controls => _controls.stream;

  final StreamController<String> _errors = StreamController.broadcast();
  Stream<String> get errors => _errors.stream;

  // Phone actions requested by the voice agent ("open spotify", "set_alarm…")
  // — {id, command} pushed by the server, executed by PhoneCommandExecutor,
  // answered with sendActionResult so Nemo can confirm out loud.
  final StreamController<Map<String, dynamic>> _actions =
      StreamController.broadcast();
  Stream<Map<String, dynamic>> get actions => _actions.stream;

  void sendActionResult(String id, {bool ok = true, String? text, String? error}) {
    final ws = _ws;
    if (ws == null) return;
    ws.sink.add(jsonEncode({
      'type': 'action_result',
      'id': id,
      'ok': ok,
      if (text != null) 'text': text,
      if (error != null) 'error': error,
    }));
  }

  Future<bool> start(String serverHost) async {
    if (_active) return true;
    _host = serverHost;
    _userStopping = false;
    _reconnectAttempts = 0;

    // One audio session for the whole chat: record captures the mic while the
    // native player (VoicePlayer.kt) plays Nemo's reply. Google-Assistant
    // semantics: TRANSIENT focus with ducking — Spotify/YouTube drop to low
    // volume while the conversation is live and resume full volume the moment
    // setActive(false) releases focus in stop().
    try {
      final session = await AudioSession.instance;
      await session.configure(const AudioSessionConfiguration(
        avAudioSessionCategory: AVAudioSessionCategory.playAndRecord,
        avAudioSessionCategoryOptions:
            AVAudioSessionCategoryOptions.defaultToSpeaker,
        avAudioSessionMode: AVAudioSessionMode.voiceChat,
        androidAudioAttributes: AndroidAudioAttributes(
          contentType: AndroidAudioContentType.speech,
          usage: AndroidAudioUsage.assistant,
        ),
        androidAudioFocusGainType:
            AndroidAudioFocusGainType.gainTransientMayDuck,
        androidWillPauseWhenDucked: false,
      ));
      await session.setActive(true);
    } catch (e) {
      debugPrint('audio session configure failed: $e');
    }

    if (!await _openSocket(_voiceUri(serverHost))) {
      _errors.add('Voice server is not reachable. Check nemo-voice service.');
      await stop();
      return false;
    }

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
        // Clean mic capture: software echo/noise/gain processing. Nemo's own
        // voice is kept out of Deepgram by half-duplex muting (the screen
        // stops forwarding mic audio while he speaks), not hardware AEC.
        echoCancel: true,
        noiseSuppress: true,
        autoGain: true,
        // Do NOT force speakerphone: that puts the system in communication
        // mode and routes Nemo's reply through the muffled call path. We play
        // on the clear media path (VoicePlayer USAGE_MEDIA), which already
        // goes to the loudspeaker; leaving the mode normal keeps it crisp.
        androidConfig: AndroidRecordConfig(
          audioSource: AndroidAudioSource.voiceCommunication,
          speakerphone: false,
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

    debugPrint('VoiceChatService: started → $serverHost');
    return true;
  }

  /// Open (or re-open) the WS to the bridge and authenticate. Returns false on
  /// failure. The recorder/player are untouched, so this also serves reconnect.
  Future<bool> _openSocket(Uri uri) async {
    // Drop any previous listener FIRST so an old connection's events can't leak
    // into the new session.
    await _wsSub?.cancel();
    _wsSub = null;
    // Read the auth payload + TLS client UP FRONT, so there are NO awaits after
    // the socket is assigned to _ws — that closes the window where a stop()
    // mid-setup could leave a half-wired zombie connection alive.
    final token = await _storage.read(key: 'app_token') ?? '';
    final voice = await _storage.read(key: 'nemo_voice') ?? 'Ethan';
    final deviceId = await _deviceId();
    final client = await SecureNet.httpClient();
    if (_userStopping) return false;
    final IOWebSocketChannel ch;
    try {
      ch = IOWebSocketChannel.connect(uri, customClient: client);
      await ch.ready.timeout(const Duration(seconds: 8));
    } catch (e) {
      return false;
    }
    if (_userStopping) {
      ch.sink.close(ws_status.goingAway);
      return false;
    }
    // No awaits past here — wire up synchronously.
    _ws = ch;
    ch.sink.add(jsonEncode({
      'type': 'auth',
      'token': token,
      'device_id': deviceId,
      'voice': voice,
    }));
    _wsSub = ch.stream.listen(
      _onMessage,
      onError: (_) => _onDrop(),
      onDone: _onDrop,
    );
    return true;
  }

  /// WS dropped. If the user didn't stop, retry with backoff instead of ending
  /// the session — the conversation resumes on the new server session.
  void _onDrop() {
    if (_userStopping || !_active) return;
    _ws = null;
    _scheduleReconnect();
  }

  /// Schedule one reconnect attempt. Guarded so overlapping drops can't spawn
  /// concurrent reconnects (at most one timer + one in-flight attempt).
  void _scheduleReconnect() {
    if (_userStopping || !_active) return;
    if (_reconnecting || (_reconnectTimer?.isActive ?? false)) return;
    if (_reconnectAttempts >= _maxReconnects) {
      _errors.add('Voice connection lost — tap to reconnect.');
      stop();
      return;
    }
    _controls.add('reconnecting');
    final delayMs = (500 * (1 << _reconnectAttempts)).clamp(500, 8000);
    _reconnectAttempts++;
    _reconnectTimer = Timer(Duration(milliseconds: delayMs), _reconnect);
  }

  Future<void> _reconnect() async {
    if (_userStopping || !_active || _host == null) return;
    _reconnecting = true;
    bool ok = false;
    try {
      ok = await _openSocket(_voiceUri(_host!));
    } finally {
      _reconnecting = false;
    }
    if (_userStopping || !_active) return;
    if (ok) {
      _reconnectAttempts = 0;
      _muted = false;
      _muteWatchdog?.cancel();
      _controls.add('reconnected');
    } else {
      _scheduleReconnect();
    }
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
        case 'agent_audio_start':
          _controls.add('agent_audio_start');
        case 'interrupted':
          _controls.add('interrupted');
        case 'ready':
          _controls.add('ready');
        case 'action':
          _actions.add(data);
        case 'error':
          _errors.add(data['message'] as String? ?? 'Voice error');
      }
    } catch (e) {
      debugPrint('VoiceChatService parse error: $e');
    }
  }

  /// A stable per-install device id, generated once and kept in secure
  /// storage, so the server can pin voice sessions to known devices.
  Future<String> _deviceId() async {
    var id = await _storage.read(key: 'device_id');
    if (id == null || id.isEmpty) {
      id = 'phone-${DateTime.now().millisecondsSinceEpoch}';
      await _storage.write(key: 'device_id', value: id);
    }
    return id;
  }

  Uri _voiceUri(String server) {
    // Accepts a bare host or the full server URL. ALWAYS wss — never cleartext,
    // even if an old stored URL says ws://. The voice bridge always lives on
    // its own port, never the main engine's. SecureNet pins the cert.
    const voicePort = 3002;
    final trimmed = server.trim();
    final parsed = Uri.tryParse(trimmed);
    final fromFullUrl = parsed != null && parsed.host.isNotEmpty;
    final host = fromFullUrl ? parsed.host : trimmed.split(':').first;
    return Uri(scheme: 'wss', host: host, port: voicePort);
  }

  Future<void> stop() async {
    _active = false;
    _userStopping = true;
    _reconnecting = false;
    _reconnectTimer?.cancel();
    _reconnectAttempts = 0;
    await _recorderSub?.cancel();
    await _recorder?.stop();
    _recorder?.dispose();
    _recorder = null;
    _recorderSub = null;
    _wsSub?.cancel();
    _ws?.sink.close(ws_status.goingAway);
    _ws = null;
    _muted = false;
    _muteWatchdog?.cancel();
    try {
      await (await AudioSession.instance).setActive(false);
    } catch (_) {}
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
    _actions.close();
    super.dispose();
  }
}
