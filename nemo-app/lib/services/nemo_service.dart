import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as status;
import 'secure_net.dart';

enum NemoState { disconnected, connecting, connected, thinking }

class NemoService extends ChangeNotifier {
  NemoState _state = NemoState.disconnected;
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  Completer<void>? _connectAck;
  bool _manualDisconnect = false;
  Timer? _reconnectTimer;

  String _serverUrl = '';
  String _token = '';
  String _deviceId = '';

  NemoState get state => _state;
  bool get isConnected => _state == NemoState.connected || _state == NemoState.thinking;
  String get serverUrl => _serverUrl;
  bool get isConfigured => _serverUrl.trim().isNotEmpty && _token.trim().isNotEmpty;

  // Incoming Nemo text replies
  final StreamController<String> _messages = StreamController.broadcast();
  Stream<String> get messages => _messages.stream;

  final StreamController<String> _errors = StreamController.broadcast();
  Stream<String> get errors => _errors.stream;

  // Incoming action commands from backend → phone executes
  final StreamController<Map<String, dynamic>> _actions =
      StreamController.broadcast();
  Stream<Map<String, dynamic>> get actions => _actions.stream;

  // Incoming audio bytes (Edge TTS) — base64 encoded MP3
  final StreamController<String> _audio = StreamController.broadcast();
  Stream<String> get audioB64 => _audio.stream;

  void configure(String serverUrl, String token, {String deviceId = ''}) {
    _serverUrl = serverUrl;
    _token = token;
    _deviceId = deviceId.isNotEmpty
        ? deviceId
        : 'phone-${DateTime.now().millisecondsSinceEpoch}';
    _manualDisconnect = false;
  }

  Future<void> connect() async {
    if (_state == NemoState.connecting) {
      try {
        await _connectAck?.future.timeout(const Duration(seconds: 8));
      } catch (_) {}
      return;
    }
    if (isConnected) return;
    if (!isConfigured) {
      _errors.add('Nemo is not paired. Add server URL and token in Settings.');
      return;
    }
    _setState(NemoState.connecting);

    try {
      // Token sent in first message, not URL — avoids proxy/log leakage.
      // wss:// uses the pinned self-signed server cert (SecureNet).
      final uri = _wsUri();
      _channel = IOWebSocketChannel.connect(
        uri,
        customClient: await SecureNet.httpClient(),
        pingInterval: const Duration(seconds: 20),
      );
      await _channel!.ready;
      // Stay "connecting" until the server's auth-gated "connected" frame
      // arrives. Showing connected here (before token validation) made a
      // bad token look like "connected but silent".

      // Send auth as first message (not in URL)
      _channel!.sink.add(jsonEncode({
        'type': 'auth',
        'token': _token,
        'device_id': _deviceId,
      }));

      _connectAck = Completer<void>();
      _sub = _channel!.stream.listen(
        _onData,
        onError: (error) => _handleDisconnect('Connection error: $error'),
        onDone: () => _handleDisconnect('Disconnected from Nemo server.'),
      );
      await _connectAck!.future.timeout(const Duration(seconds: 8));
    } catch (e) {
      debugPrint('NemoService connect error: $e');
      _errors.add('Could not connect to Nemo. Check that the server is running.');
      _handleDisconnect(null);
    }
  }

  void _onData(dynamic raw) {
    try {
      final data = jsonDecode(raw as String) as Map<String, dynamic>;
      final type = data['type'] as String? ?? '';

      switch (type) {
        case 'connected':
          _setState(NemoState.connected);
          if (_connectAck != null && !_connectAck!.isCompleted) {
            _connectAck!.complete();
          }

        case 'message':
          final text = data['text'] as String? ?? '';
          if (text.isNotEmpty) {
            _messages.add(text);
            _setState(NemoState.connected);
          }

        case 'audio':
          // Edge TTS audio from backend — base64 MP3
          final b64 = data['data'] as String? ?? '';
          if (b64.isNotEmpty) _audio.add(b64);

        case 'action':
          // Backend wants phone to do something
          _actions.add(data);

        default:
          debugPrint('unknown msg type: $type');
      }
    } catch (e) {
      debugPrint('NemoService parse error: $e');
    }
  }

  Future<bool> send(String text) async {
    if (!isConnected) await connect();
    if (!isConnected || _channel == null) {
      _errors.add('Message not sent. Nemo server is not connected.');
      return false;
    }
    _channel!.sink.add(jsonEncode({'type': 'message', 'text': text}));
    _setState(NemoState.thinking);
    return true;
  }

  /// Send a message with a media attachment (image/PDF as base64).
  Future<bool> sendWithMedia(String text, String b64, String mimeType) async {
    if (!isConnected) await connect();
    if (!isConnected || _channel == null) {
      _errors.add('Media not sent. Nemo server is not connected.');
      return false;
    }
    _channel!.sink.add(jsonEncode({
      'type': 'message',
      'text': text,
      'media': {'data': b64, 'mime': mimeType},
    }));
    _setState(NemoState.thinking);
    return true;
  }

  /// Send action result back to backend after phone executes a command.
  void sendPanic() {
    if (_channel == null) return;
    try {
      _channel!.sink.add(jsonEncode({'type': 'panic'}));
      debugPrint('Panic signal sent');
    } catch (_) {}
    disconnect();
  }

  void sendActionResult(String actionId, {bool ok = true, String? text, String? error, String? imageB64}) {
    if (_channel == null) return;
    final result = <String, dynamic>{
      'type': 'action_result',
      'id': actionId,
      'ok': ok,
    };
    if (text != null) result['text'] = text;
    if (error != null) result['error'] = error;
    if (imageB64 != null) result['image_b64'] = imageB64;
    _channel!.sink.add(jsonEncode(result));
  }

  Uri _wsUri() {
    final base = Uri.parse(_serverUrl.trim());
    final path = base.path.endsWith('/ws') ? base.path : '${base.path}/ws';
    return base.replace(
      // Force TLS: never connect in cleartext, even if an old stored URL says
      // ws://. The server is wss-only; SecureNet pins the cert.
      scheme: 'wss',
      path: path,
      queryParameters: {
        ...base.queryParameters,
        'device_id': _deviceId,
      },
    );
  }

  void _handleDisconnect(String? reason) {
    _sub?.cancel();
    _channel?.sink.close(status.goingAway);
    _channel = null;
    _sub = null;
    if (_connectAck != null && !_connectAck!.isCompleted) {
      _connectAck!.completeError(reason ?? 'disconnected');
    }
    _connectAck = null;
    _setState(NemoState.disconnected);
    if (reason != null) _errors.add(reason);
    if (!_manualDisconnect && isConfigured) {
      _reconnectTimer?.cancel();
      _reconnectTimer = Timer(const Duration(seconds: 5), () {
        if (_state == NemoState.disconnected) connect();
      });
    }
  }

  void disconnect() {
    _manualDisconnect = true;
    _reconnectTimer?.cancel();
    _handleDisconnect(null);
  }

  void _setState(NemoState s) {
    if (_state == s) return;
    _state = s;
    notifyListeners();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _reconnectTimer?.cancel();
    _channel?.sink.close(status.goingAway);
    _messages.close();
    _errors.close();
    _actions.close();
    _audio.close();
    super.dispose();
  }
}
