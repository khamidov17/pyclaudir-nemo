import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../services/voice_session_controller.dart';
import '../theme.dart';
import '../widgets/voice_orb.dart';

/// Window onto the app-level voice session (VoiceSessionController).
///
/// The session itself lives above the UI — it starts from the wake word even
/// when this screen was never opened, and KEEPS RUNNING if the user backs out
/// of it. This screen only displays transcripts and toggles the session.
class VoiceChatScreen extends StatefulWidget {
  final bool autoStart;
  const VoiceChatScreen({super.key, this.autoStart = false});

  @override
  State<VoiceChatScreen> createState() => _VoiceChatScreenState();
}

class _VoiceChatScreenState extends State<VoiceChatScreen> {
  late VoiceSessionController _session;
  StreamSubscription? _errorSub;

  @override
  void initState() {
    super.initState();
    _session = context.read<VoiceSessionController>();
    if (widget.autoStart && !_session.isActive) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _session.start());
    }
    _errorSub = _session.errors.listen((message) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(message)),
      );
    });
  }

  OrbState _orbState() {
    if (!_session.isActive) {
      return _session.idleClosed ? OrbState.idle : OrbState.connecting;
    }
    return _session.nemoSpeaking ? OrbState.speaking : OrbState.listening;
  }

  String _statusText() {
    if (!_session.isActive) {
      return _session.idleClosed ? 'Paused — tap to talk' : 'Connecting…';
    }
    return _session.nemoSpeaking ? 'Nemo is speaking' : 'Listening';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          icon: const Icon(Icons.expand_more),
          tooltip: 'Back (keeps talking)',
          // Back does NOT end the call — the session continues hands-free.
          onPressed: () => Navigator.pop(context),
        ),
        actions: [
          ListenableBuilder(
            listenable: _session,
            builder: (_, __) => _session.isActive
                ? IconButton(
                    tooltip: 'End conversation',
                    icon: const Icon(Icons.call_end, color: NemoColors.danger),
                    onPressed: _session.stop,
                  )
                : const SizedBox.shrink(),
          ),
        ],
      ),
      body: ListenableBuilder(
        listenable: _session,
        builder: (_, __) => Column(
          children: [
            // Orb + status centered in the space above the transcript.
            Expanded(
              child: Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    VoiceOrb(
                      state: _orbState(),
                      size: 260,
                      onTap: _session.toggle,
                    ),
                    const SizedBox(height: 40),
                    Text(
                      _statusText(),
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: NemoColors.text,
                        fontSize: 22,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 0.4,
                      ),
                    ),
                    const SizedBox(height: 10),
                    Text(
                      _session.isActive ? 'Tap orb to pause' : 'Tap orb to talk',
                      style: const TextStyle(
                        color: NemoColors.textFaint,
                        fontSize: 13,
                      ),
                    ),
                  ],
                ),
              ),
            ),
            // No on-screen transcript/log — the conversation is saved to
            // memory server-side, not shown here. Just the orb + status.
            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }

  @override
  void dispose() {
    _errorSub?.cancel();
    super.dispose();
  }
}
