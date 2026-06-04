import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as status;

enum NemoState { disconnected, connecting, connected, thinking }

class NemoService extends ChangeNotifier {
  NemoState _state = NemoState.disconnected;
  WebSocketChannel? _channel;
  StreamSubscription? _sub;

  String _serverUrl = '';
  String _token = '';
  String _deviceId = '';

  NemoState get state => _state;
  bool get isConnected => _state == NemoState.connected || _state == NemoState.thinking;
  String get serverUrl => _serverUrl;

  // Incoming Nemo text replies
  final StreamController<String> _messages = StreamController.broadcast();
  Stream<String> get messages => _messages.stream;

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
  }

  Future<void> connect() async {
    if (_state == NemoState.connecting || isConnected) return;
    _setState(NemoState.connecting);

    try {
      // Token sent in first message, not URL — avoids proxy/log leakage
      final uri = Uri.parse('$_serverUrl/ws?device_id=$_deviceId');
      _channel = WebSocketChannel.connect(uri);
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

      _sub = _channel!.stream.listen(
        _onData,
        onError: (_) => _reconnect(),
        onDone: _reconnect,
      );
    } catch (e) {
      debugPrint('NemoService connect error: $e');
      _reconnect();
    }
  }

  void _onData(dynamic raw) {
    try {
      final data = jsonDecode(raw as String) as Map<String, dynamic>;
      final type = data['type'] as String? ?? '';

      switch (type) {
        case 'connected':
          _setState(NemoState.connected);

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

  Future<void> send(String text) async {
    if (!isConnected) await connect();
    if (_channel == null) return;
    _channel!.sink.add(jsonEncode({'type': 'message', 'text': text}));
    _setState(NemoState.thinking);
  }

  /// Send a message with a media attachment (image/PDF as base64).
  Future<void> sendWithMedia(String text, String b64, String mimeType) async {
    if (!isConnected) await connect();
    if (_channel == null) return;
    _channel!.sink.add(jsonEncode({
      'type': 'message',
      'text': text,
      'media': {'data': b64, 'mime': mimeType},
    }));
    _setState(NemoState.thinking);
  }

  /// Send action result back to backend after phone executes a command.
  void sendPanic() {
    if (_channel == null) return;
    try {
      _channel!.sink.add(jsonEncode({'type': 'panic'}));
      debugPrint('Panic signal sent');
    } catch (_) {}
    _reconnect(); // Disconnect immediately
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

  void _reconnect() {
    _sub?.cancel();
    _channel?.sink.close(status.goingAway);
    _channel = null;
    _sub = null;
    _setState(NemoState.disconnected);
    // Reconnect after 3s
    Future.delayed(const Duration(seconds: 3), () {
      if (_state == NemoState.disconnected) connect();
    });
  }

  void _setState(NemoState s) {
    if (_state == s) return;
    _state = s;
    notifyListeners();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _channel?.sink.close(status.goingAway);
    _messages.close();
    _actions.close();
    _audio.close();
    super.dispose();
  }
}
