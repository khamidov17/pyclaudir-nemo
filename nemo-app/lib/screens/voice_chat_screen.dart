// ignore_for_file: experimental_member_use

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../services/voice_chat_service.dart';
import '../services/native_voice_player.dart';
import '../services/wake_word_service.dart';

/// Full-screen Nemo Voice chat — tap to talk, Nemo talks back.
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
  WakeWordService? _wake;
  late AnimationController _pulse;
  late Animation<double> _pulseAnim;
  // Native AudioTrack streaming player on the voice-comms path — plays Nemo's
  // 24kHz PCM chunk-by-chunk as it arrives (full-duplex, barge-in capable).
  final NativeVoicePlayer _player = NativeVoicePlayer();
  final List<String> _log = [];
  StreamSubscription? _transcriptSub;
  StreamSubscription? _audioSub;
  StreamSubscription? _controlSub;
  StreamSubscription? _errorSub;
  // Estimated wall-clock time (ms since epoch) at which all audio handed to
  // the player so far will have finished playing. Used to resume the mic the
  // instant Nemo actually stops speaking — not before (echo) or much after.
  int _estPlaybackEndMs = 0;
  Timer? _unmuteTimer;
  bool _nemoSpeaking = false;

  @override
  void initState() {
    super.initState();
    // Wake word and voice chat both need the microphone, and Android only
    // grants it to one consumer. Release the wake-word recognizer for the
    // whole lifetime of this screen so the voice recorder can capture audio.
    _wake = context.read<WakeWordService>();
    // Always auto-start — opening the voice screen means "start talking".
    // No tap required (wake word or the voice button both land here).
    WidgetsBinding.instance.addPostFrameCallback((_) => _toggleVoice());
    _pulse = AnimationController(vsync: this, duration: const Duration(milliseconds: 800))
      ..repeat(reverse: true);
    _pulseAnim = Tween<double>(begin: 1.0, end: 1.3).animate(
      CurvedAnimation(parent: _pulse, curve: Curves.easeInOut),
    );
    _voice = VoiceChatService();
    _transcriptSub = _voice.transcripts.listen((t) {
      setState(() => _log.add(t));
    });
    // Stream each agent PCM chunk straight to the native player — no per-turn
    // buffering — and advance the estimated playback-end clock (24kHz·16-bit
    // mono = 48000 bytes/sec) so we know when Nemo will actually stop talking.
    _audioSub = _voice.audioOut.listen((chunk) {
      _player.write(chunk);
      final now = DateTime.now().millisecondsSinceEpoch;
      if (_estPlaybackEndMs < now) _estPlaybackEndMs = now;
      _estPlaybackEndMs += (chunk.length / 48000 * 1000).round();
    });
    _controlSub = _voice.controls.listen((signal) {
      if (signal == 'agent_audio_start') {
        // Nemo started talking → close the mic so his voice can't echo back
        // into Deepgram and cut him off.
        _unmuteTimer?.cancel();
        _voice.setMuted(true);
        if (mounted) setState(() => _nemoSpeaking = true);
      } else if (signal == 'turn_complete') {
        // Resume the mic only once the buffered audio has finished playing
        // (plus a small tail for the speaker to settle), so the very end of
        // his sentence doesn't leak back as a phantom user turn.
        final remaining =
            _estPlaybackEndMs - DateTime.now().millisecondsSinceEpoch + 350;
        _unmuteTimer?.cancel();
        _unmuteTimer = Timer(
          Duration(milliseconds: remaining.clamp(0, 6000)),
          () {
            _voice.setMuted(false);
            if (mounted) setState(() => _nemoSpeaking = false);
          },
        );
      } else if (signal == 'interrupted') {
        _player.flush();
        _estPlaybackEndMs = 0;
        _unmuteTimer?.cancel();
        _voice.setMuted(false);
        if (mounted) setState(() => _nemoSpeaking = false);
      }
    });
    _errorSub = _voice.errors.listen((message) {
      if (!mounted) return;
      setState(() => _log.add(message));
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(message)),
      );
    });
  }

  Future<void> _toggleVoice() async {
    if (_voice.isActive) {
      await _voice.stop();
      await _player.stop();
    } else {
      // Free the mic from the wake-word recognizer before recording. Give
      // Android a moment to fully release the audio input.
      await _wake?.stop();
      await Future.delayed(const Duration(milliseconds: 300));
      // Open the native playback path before the mic so MODE_IN_COMMUNICATION
      // + hardware AEC are active when the first agent audio arrives.
      await _player.start(sampleRate: 24000);
      final started = await _voice.start(widget.serverHost);
      if (!mounted) return;
      if (started) {
        setState(() => _log.add('Voice chat started - speak now'));
      } else {
        await _player.stop();
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
            await _player.stop();
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
                    !_voice.isActive
                        ? 'Connecting… (tap to retry)'
                        : _nemoSpeaking
                            ? 'Nemo is speaking…'
                            : 'Listening…',
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
                          color: !_voice.isActive
                              ? const Color(0xFF1E1E2E)
                              : _nemoSpeaking
                                  ? const Color(0xFF22C55E)
                                  : const Color(0xFF7C3AED),
                          boxShadow: _voice.isActive
                              ? [BoxShadow(
                                  color: (_nemoSpeaking
                                          ? const Color(0xFF22C55E)
                                          : const Color(0xFF7C3AED))
                                      .withValues(alpha: 0.4),
                                  blurRadius: 24, spreadRadius: 4,
                                )]
                              : [],
                        ),
                        child: Icon(
                          !_voice.isActive
                              ? Icons.mic
                              : _nemoSpeaking
                                  ? Icons.graphic_eq
                                  : Icons.mic,
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
    _player.stop();
    // Resume wake word (no-op if the user disabled it in Settings).
    _wake?.start();
    _transcriptSub?.cancel();
    _audioSub?.cancel();
    _controlSub?.cancel();
    _errorSub?.cancel();
    _unmuteTimer?.cancel();
    _pulse.dispose();
    super.dispose();
  }
}
