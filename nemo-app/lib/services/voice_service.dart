import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:speech_to_text/speech_to_text.dart';

class VoiceService extends ChangeNotifier {
  final SpeechToText _stt = SpeechToText();
  final FlutterTts _tts = FlutterTts();
  final AudioPlayer _player = AudioPlayer(); // for Edge TTS audio

  bool _sttReady = false;
  bool _listening = false;
  bool _speaking = false;

  bool get isListening => _listening;
  bool get isSpeaking => _speaking;

  final ValueNotifier<String> partialResult = ValueNotifier('');

  Future<void> init() async {
    _sttReady = await _stt.initialize(
      onStatus: (s) {
        if (s == 'done' || s == 'notListening') {
          _listening = false;
          notifyListeners();
        }
      },
      onError: (e) {
        debugPrint('STT error: $e');
        _listening = false;
        notifyListeners();
      },
    );

    await _tts.setLanguage('en-US');
    await _tts.setSpeechRate(0.9);
    await _tts.setVolume(1.0);
    await _tts.setPitch(1.0);

    _tts.setCompletionHandler(() {
      _speaking = false;
      notifyListeners();
    });
  }

  /// Start listening and return the recognised text when done.
  Future<String> listen({Duration timeout = const Duration(seconds: 15)}) async {
    if (!_sttReady || _listening) return '';

    final completer = Completer<String>();
    _listening = true;
    partialResult.value = '';
    notifyListeners();

    await _stt.listen(
      onResult: (result) {
        partialResult.value = result.recognizedWords;
        if (result.finalResult) {
          _listening = false;
          notifyListeners();
          if (!completer.isCompleted) {
            completer.complete(result.recognizedWords);
          }
        }
      },
      listenFor: timeout,
      pauseFor: const Duration(seconds: 3),
      localeId: 'en_US',
      cancelOnError: true,
    );

    // Timeout fallback
    Future.delayed(timeout + const Duration(seconds: 2), () {
      if (!completer.isCompleted) {
        stopListening();
        completer.complete(partialResult.value);
      }
    });

    return completer.future;
  }

  Future<void> stopListening() async {
    await _stt.stop();
    _listening = false;
    notifyListeners();
  }

  /// Play Edge TTS audio (base64 MP3 from backend). Falls back to device TTS.
  Future<void> playAudio(String b64) async {
    if (_listening) await stopListening();
    _speaking = true;
    notifyListeners();
    try {
      final bytes = base64Decode(b64);
      final dir = await getTemporaryDirectory();
      final file = File('${dir.path}/nemo_tts.mp3');
      await file.writeAsBytes(bytes);
      await _player.setFilePath(file.path);
      await _player.play();
      await _player.processingStateStream
          .firstWhere((s) => s == ProcessingState.completed)
          .timeout(const Duration(seconds: 30));
    } catch (e) {
      debugPrint('audio playback error: $e');
    } finally {
      _speaking = false;
      notifyListeners();
    }
  }

  Future<void> speak(String text) async {
    // Fallback device TTS — only used if Edge TTS audio not available
    if (_listening) await stopListening();
    _speaking = true;
    notifyListeners();
    await _tts.speak(text);
  }

  Future<void> stopSpeaking() async {
    await _player.stop();
    await _tts.stop();
    _speaking = false;
    notifyListeners();
  }
}
