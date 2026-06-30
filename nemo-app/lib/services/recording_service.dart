import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'secure_net.dart';

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

/// Ambient conversation recorder.
/// Records audio continuously, then transcribes via Groq Whisper and
/// saves transcript to Nemo's memory for future recall.
class RecordingService extends ChangeNotifier {
  final AudioRecorder _recorder = AudioRecorder();
  bool _recording = false;
  bool _voiceSessionActive = false;
  String? _currentPath;
  DateTime? _startTime;
  Timer? _timer;
  int _elapsed = 0;

  bool get isRecording => _recording;
  int get elapsedSeconds => _elapsed;
  String? get currentPath => _currentPath;

  void setVoiceSessionActive(bool active) {
    _voiceSessionActive = active;
  }

  Future<void> startRecording() async {
    if (_recording) return;
    if (_voiceSessionActive) {
      debugPrint('RecordingService: cannot start — voice session active');
      return;
    }
    final dir = await getApplicationDocumentsDirectory();
    final ts = DateTime.now().millisecondsSinceEpoch;
    _currentPath = '${dir.path}/nemo_recording_$ts.m4a';
    try {
      await _recorder.start(
        RecordConfig(encoder: AudioEncoder.aacLc, bitRate: 64000),
        path: _currentPath!,
      );
    } catch (e) {
      _currentPath = null;
      debugPrint('RecordingService: start failed: $e');
      notifyListeners();
      return;
    }
    _recording = true;
    _startTime = DateTime.now();
    _elapsed = 0;
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      _elapsed++;
      notifyListeners();
    });
    notifyListeners();
    debugPrint('Recording started: $_currentPath');
  }

  Future<RecordingResult?> stopAndTranscribe() async {
    if (!_recording) return null;
    _timer?.cancel();
    final path = _currentPath!;
    final start = _startTime!;
    await _recorder.stop();
    _recording = false;
    _currentPath = null;
    notifyListeners();

    debugPrint('Recording stopped, transcribing...');
    final transcript = await _transcribe(path);
    return RecordingResult(
      audioPath: path,
      transcript: transcript,
      startTime: start,
      endTime: DateTime.now(),
    );
  }

  Future<String?> _transcribe(String audioPath) async {
    final groqKey = await _getGroqKey();
    if (groqKey == null || groqKey.isEmpty) {
      debugPrint('No Groq API key for transcription');
      return null;
    }
    for (int attempt = 0; attempt < 3; attempt++) {
      if (attempt > 0) {
        await Future<void>.delayed(Duration(seconds: 2 * attempt));
      }
      try {
        final file = File(audioPath);
        final req = http.MultipartRequest(
          'POST',
          Uri.parse('https://api.groq.com/openai/v1/audio/transcriptions'),
        )
          ..headers['Authorization'] = 'Bearer $groqKey'
          ..fields['model'] = 'whisper-large-v3-turbo'
          ..fields['response_format'] = 'text'
          ..files.add(await http.MultipartFile.fromPath('file', file.path));
        final resp = await req.send().timeout(const Duration(minutes: 5));
        final body = await resp.stream.bytesToString();
        if (resp.statusCode == 200) return body.trim();
        debugPrint('Groq error ${resp.statusCode} (attempt ${attempt + 1}): $body');
        if (resp.statusCode < 500) break; // non-retriable (auth, bad request)
      } catch (e) {
        debugPrint('Groq transcription attempt ${attempt + 1} failed: $e');
      }
    }
    return null;
  }

  Future<String?> _getGroqKey() async {
    // Get from server via health endpoint if not stored locally
    try {
      final serverUrl = await _storage.read(key: 'server_url') ?? '';
      final token = await _storage.read(key: 'app_token') ?? '';
      if (serverUrl.isEmpty || token.isEmpty) return null;
      final httpUrl = serverUrl.replaceFirst('ws://', 'https://').replaceFirst('wss://', 'https://');
      final client = IOClient(await SecureNet.httpClient());
      final http.Response resp;
      try {
        resp = await client
            .get(
              Uri.parse('$httpUrl/groq-key'),
              headers: {'Authorization': 'Bearer $token'},
            )
            .timeout(const Duration(seconds: 5));
      } finally {
        client.close();
      }
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        return data['key'] as String?;
      }
    } catch (_) {}
    return null;
  }

  @override
  void dispose() {
    _timer?.cancel();
    _recorder.dispose();
    super.dispose();
  }
}

class RecordingResult {
  final String audioPath;
  final String? transcript;
  final DateTime startTime;
  final DateTime endTime;
  RecordingResult({
    required this.audioPath,
    required this.transcript,
    required this.startTime,
    required this.endTime,
  });
  Duration get duration => endTime.difference(startTime);
}
