// ignore_for_file: experimental_member_use

import 'dart:async';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';
import 'dart:io';
import '../services/voice_chat_service.dart';

/// Full-screen Gemini Live voice chat — tap to talk, Nemo talks back.
class VoiceChatScreen extends StatefulWidget {
  final String serverHost;
  final bool autoStart;
  const VoiceChatScreen({
    super.key,
    required this.serverHost,
    this.autoStart = false,
  });

  @override
  State<VoiceChatScreen> createState() => _VoiceChatScreenState();
}

class _VoiceChatScreenState extends State<VoiceChatScreen>
    with SingleTickerProviderStateMixin {
  late VoiceChatService _voice;
  late AnimationController _pulse;
  late Animation<double> _pulseAnim;
  final AudioPlayer _player = AudioPlayer();
  final List<String> _log = [];
  StreamSubscription? _transcriptSub;
  StreamSubscription? _audioSub;
  StreamSubscription? _errorSub;

  // Buffer for incoming PCM audio chunks (24kHz)
  final List<Uint8List> _audioBuffer = [];
  Timer? _playTimer;

  @override
  void initState() {
    super.initState();
    // Auto-start if triggered by wake word — no tap needed
    if (widget.autoStart) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _toggleVoice());
    }
    _pulse = AnimationController(vsync: this, duration: const Duration(milliseconds: 800))
      ..repeat(reverse: true);
    _pulseAnim = Tween<double>(begin: 1.0, end: 1.3).animate(
      CurvedAnimation(parent: _pulse, curve: Curves.easeInOut),
    );
    _voice = VoiceChatService();
    _transcriptSub = _voice.transcripts.listen((t) {
      setState(() => _log.add(t));
    });
    _audioSub = _voice.audioOut.listen(_bufferAudio);
    _errorSub = _voice.errors.listen((message) {
      if (!mounted) return;
      setState(() => _log.add(message));
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(message)),
      );
    });
  }

  void _bufferAudio(Uint8List chunk) {
    _audioBuffer.add(chunk);
    _playTimer?.cancel();
    // Batch chunks for 200ms then play
    _playTimer = Timer(const Duration(milliseconds: 200), _flushAudio);
  }

  Future<void> _flushAudio() async {
    if (_audioBuffer.isEmpty) return;
    final combined = Uint8List.fromList(_audioBuffer.expand((c) => c).toList());
    _audioBuffer.clear();
    try {
      final dir = await getTemporaryDirectory();
      final file = File('${dir.path}/nemo_voice_chunk.pcm');
      await file.writeAsBytes(combined);
      // Play raw PCM 24kHz mono 16-bit
      await _player.setAudioSource(
        _PCMSource(file.path, sampleRate: 24000),
      );
      await _player.play();
    } catch (e) {
      debugPrint('audio play error: $e');
    }
  }

  Future<void> _toggleVoice() async {
    if (_voice.isActive) {
      await _voice.stop();
    } else {
      final started = await _voice.start(widget.serverHost);
      if (!mounted) return;
      if (started) {
        setState(() => _log.add('Voice chat started - speak now'));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F0F1A),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0F0F1A),
        title: const Text('Voice Chat'),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () async {
            await _voice.stop();
            if (mounted) Navigator.pop(context);
          },
        ),
      ),
      body: Column(
        children: [
          // Transcripts
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.all(16),
              itemCount: _log.length,
              itemBuilder: (_, i) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Text(
                  _log[i],
                  style: TextStyle(
                    color: _log[i].startsWith('You:')
                        ? Colors.white70
                        : const Color(0xFF7C3AED),
                    fontSize: 15,
                  ),
                ),
              ),
            ),
          ),

          // Big mic button
          Padding(
            padding: const EdgeInsets.only(bottom: 60),
            child: ListenableBuilder(
              listenable: _voice,
              builder: (_, __) => Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    _voice.isActive ? 'Listening…' : 'Tap to start',
                    style: const TextStyle(color: Colors.white38, fontSize: 14),
                  ),
                  const SizedBox(height: 20),
                  GestureDetector(
                    onTap: _toggleVoice,
                    child: AnimatedBuilder(
                      animation: _pulseAnim,
                      builder: (_, child) => Transform.scale(
                        scale: _voice.isActive ? _pulseAnim.value : 1.0,
                        child: child,
                      ),
                      child: Container(
                        width: 90, height: 90,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: _voice.isActive
                              ? const Color(0xFF7C3AED)
                              : const Color(0xFF1E1E2E),
                          boxShadow: _voice.isActive
                              ? [const BoxShadow(
                                  color: Color(0x667C3AED),
                                  blurRadius: 20, spreadRadius: 4,
                                )]
                              : [],
                        ),
                        child: Icon(
                          _voice.isActive ? Icons.stop : Icons.mic,
                          color: Colors.white,
                          size: 40,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _voice.stop();
    _voice.dispose();
    _transcriptSub?.cancel();
    _audioSub?.cancel();
    _errorSub?.cancel();
    _player.dispose();
    _pulse.dispose();
    _playTimer?.cancel();
    super.dispose();
  }
}

// Minimal AudioSource wrapper for raw PCM — just_audio can't play raw PCM
// directly, so we convert to WAV header + data
class _PCMSource extends StreamAudioSource {
  final String path;
  final int sampleRate;
  _PCMSource(this.path, {this.sampleRate = 24000}) : super(tag: 'pcm');

  @override
  Future<StreamAudioResponse> request([int? start, int? end]) async {
    final pcm = await File(path).readAsBytes();
    final wav = _wrapWav(pcm, sampleRate);
    final slice = wav.sublist(start ?? 0, end ?? wav.length);
    return StreamAudioResponse(
      sourceLength: wav.length,
      contentLength: slice.length,
      offset: start ?? 0,
      stream: Stream.value(slice),
      contentType: 'audio/wav',
    );
  }

  Uint8List _wrapWav(Uint8List pcm, int rate) {
    final dataLen = pcm.length;
    final totalLen = dataLen + 36;
    final buf = ByteData(44 + dataLen);
    // RIFF header
    final riff = ascii('RIFF');
    for (var i = 0; i < 4; i++) buf.setUint8(i, riff[i]);
    buf.setUint32(4, totalLen, Endian.little);
    final wave = ascii('WAVE');
    for (var i = 0; i < 4; i++) buf.setUint8(8 + i, wave[i]);
    final fmt = ascii('fmt ');
    for (var i = 0; i < 4; i++) buf.setUint8(12 + i, fmt[i]);
    buf.setUint32(16, 16, Endian.little); // chunk size
    buf.setUint16(20, 1, Endian.little);  // PCM
    buf.setUint16(22, 1, Endian.little);  // mono
    buf.setUint32(24, rate, Endian.little);
    buf.setUint32(28, rate * 2, Endian.little); // byte rate
    buf.setUint16(32, 2, Endian.little);  // block align
    buf.setUint16(34, 16, Endian.little); // bits per sample
    final data = ascii('data');
    for (var i = 0; i < 4; i++) buf.setUint8(36 + i, data[i]);
    buf.setUint32(40, dataLen, Endian.little);
    final result = buf.buffer.asUint8List();
    result.setRange(44, 44 + dataLen, pcm);
    return result;
  }

  List<int> ascii(String s) => s.codeUnits;
}
