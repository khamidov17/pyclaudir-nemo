import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:permission_handler/permission_handler.dart';

import '../app_version.dart';
import '../models/message.dart';
import '../services/nemo_service.dart';
import '../services/update_service.dart';
import '../services/voice_service.dart';
import '../services/wake_word_service.dart';
import '../widgets/chat_bubble.dart';
import 'settings_screen.dart';
import 'voice_chat_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen>
    with SingleTickerProviderStateMixin {
  final List<Message> _messages = [];
  final TextEditingController _textCtrl = TextEditingController();
  final ScrollController _scroll = ScrollController();
  late AnimationController _pulseCtrl;
  late Animation<double> _pulse;
  StreamSubscription? _errorSub;

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    )..repeat(reverse: true);
    _pulse = Tween<double>(begin: 1.0, end: 1.25).animate(
      CurvedAnimation(parent: _pulseCtrl, curve: Curves.easeInOut),
    );
    _requestPermissions();
    _init();
  }

  Future<void> _requestPermissions() async {
    await [Permission.microphone, Permission.speech].request();
  }

  Future<void> _init() async {
    final voice = context.read<VoiceService>();
    final nemo = context.read<NemoService>();
    final wake = context.read<WakeWordService>();

    await voice.init();
    _errorSub = nemo.errors.listen((message) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(message)),
      );
    });
    await nemo.connect();

    nemo.messages.listen((text) {
      _addMessage(text, Sender.nemo);
      // Don't speak here — audio comes via Edge TTS in audioB64 stream
      // If no audio arrives within 3s, fall back to device TTS
    });

    // Play Edge TTS audio when it arrives from backend
    nemo.audioB64.listen((b64) => voice.playAudio(b64));

    // Wake word → immediately open Gemini Live voice chat (no tap needed)
    wake.onWakeWord = () async {
      if (!mounted) return;
      await wake.stop();
      final serverHost = _extractHost(nemo.serverUrl);

      // Auto-open voice chat — no button, no confirmation, immediate
      await Navigator.push(
        context,
        PageRouteBuilder(
          pageBuilder: (_, __, ___) => VoiceChatScreen(
            serverHost: serverHost,
            autoStart: true, // starts immediately on open
          ),
          transitionDuration: const Duration(milliseconds: 150),
          transitionsBuilder: (_, anim, __, child) =>
              FadeTransition(opacity: anim, child: child),
        ),
      );

      if (mounted) await wake.start();
    };

    await Future.delayed(const Duration(milliseconds: 600));
    await wake.start();
  }

  String _extractHost(String wsUrl) {
    try {
      final uri = Uri.parse(wsUrl);
      return uri.host;
    } catch (_) {
      return wsUrl.replaceAll(RegExp(r'^wss?://'), '').split(':').first;
    }
  }

  Future<void> _startVoiceInput() async {
    final voice = context.read<VoiceService>();
    final nemo = context.read<NemoService>();

    if (voice.isSpeaking) await voice.stopSpeaking();
    final text = await voice.listen();
    if (text.trim().isEmpty) return;

    _addMessage(text, Sender.user);
    await nemo.send(text);
  }

  Future<void> _sendText() async {
    final text = _textCtrl.text.trim();
    if (text.isEmpty) return;
    _textCtrl.clear();
    _addMessage(text, Sender.user);
    await context.read<NemoService>().send(text);
  }

  void _addMessage(String text, Sender sender) {
    setState(() {
      _messages.add(Message(id: DateTime.now().toIso8601String(), sessionId: 'home', text: text, sender: sender, time: DateTime.now()));
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final nemo = context.watch<NemoService>();
    final voice = context.watch<VoiceService>();
    final primary = Theme.of(context).colorScheme.primary;

    return Scaffold(
      backgroundColor: const Color(0xFF0F0F1A),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0F0F1A),
        elevation: 0,
        title: Row(
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 300),
              width: 8, height: 8,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: nemo.isConnected ? Colors.greenAccent : Colors.redAccent,
                boxShadow: nemo.isConnected
                    ? [BoxShadow(color: Colors.greenAccent.withValues(alpha: 0.5), blurRadius: 6)]
                    : [],
              ),
            ),
            const SizedBox(width: 8),
            const Text('Nemo $nemoVersionLabel',
                style: TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(width: 6),
            if (nemo.state == NemoState.thinking)
              const Text('thinking…',
                  style: TextStyle(fontSize: 12, color: Colors.white38)),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.record_voice_over),
            tooltip: 'Gemini Voice Chat',
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => VoiceChatScreen(
                  serverHost: _extractHost(
                      context.read<NemoService>().serverUrl),
                ),
              ),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.settings_outlined),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const SettingsScreen()),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: _messages.isEmpty
                ? Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text('👋', style: const TextStyle(fontSize: 52)),
                        const SizedBox(height: 14),
                        Text(
                          'Say "Nemo" or tap the mic',
                          style: TextStyle(color: Colors.white38, fontSize: 15),
                        ),
                      ],
                    ),
                  )
                : ListView.builder(
                    controller: _scroll,
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    itemCount: _messages.length,
                    itemBuilder: (_, i) => ChatBubble(message: _messages[i]),
                  ),
          ),
          // OTA update banner
          Consumer<UpdateService>(
            builder: (_, updater, __) {
              if (!updater.updateAvailable && !updater.isDownloading) return const SizedBox.shrink();
              return Container(
                color: const Color(0xFF7C3AED),
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                child: updater.isDownloading
                    ? Row(children: [
                        const SizedBox(width: 14, height: 14,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white)),
                        const SizedBox(width: 10),
                        Text('Downloading ${(updater.downloadProgress * 100).toStringAsFixed(0)}%…',
                            style: const TextStyle(color: Colors.white, fontSize: 13)),
                      ])
                    : Row(children: [
                        const Icon(Icons.system_update, color: Colors.white, size: 18),
                        const SizedBox(width: 8),
                        Expanded(child: Text('Nemo v${updater.serverVersion} available',
                            style: const TextStyle(color: Colors.white, fontSize: 13))),
                        TextButton(
                          onPressed: () {
                            final url = context.read<NemoService>().serverUrl;
                            context.read<UpdateService>().downloadAndInstall(url);
                          },
                          child: const Text('Update', style: TextStyle(
                              color: Colors.white, fontWeight: FontWeight.bold)),
                        ),
                      ]),
              );
            },
          ),
          if (voice.isListening)
            ValueListenableBuilder<String>(
              valueListenable: voice.partialResult,
              builder: (_, val, __) => val.isEmpty
                  ? const SizedBox.shrink()
                  : Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 4),
                      child: Text(val,
                          style: const TextStyle(
                              color: Colors.white38, fontSize: 13),
                          textAlign: TextAlign.center),
                    ),
            ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _textCtrl,
                      style: const TextStyle(color: Colors.white),
                      decoration: InputDecoration(
                        hintText: 'Message Nemo…',
                        hintStyle: const TextStyle(color: Colors.white38),
                        filled: true,
                        fillColor: const Color(0xFF1E1E2E),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(24),
                          borderSide: BorderSide.none,
                        ),
                        contentPadding: const EdgeInsets.symmetric(
                            horizontal: 16, vertical: 12),
                      ),
                      onSubmitted: (_) => _sendText(),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton(
                    icon: const Icon(Icons.send_rounded),
                    color: primary,
                    onPressed: _sendText,
                  ),
                  // Mic button with pulse animation
                  GestureDetector(
                    onTap: voice.isListening
                        ? voice.stopListening
                        : _startVoiceInput,
                    child: AnimatedBuilder(
                      animation: _pulse,
                      builder: (_, child) => Transform.scale(
                        scale: voice.isListening ? _pulse.value : 1.0,
                        child: child,
                      ),
                      child: Container(
                        width: 52,
                        height: 52,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: voice.isListening
                              ? primary
                              : const Color(0xFF1E1E2E),
                          boxShadow: voice.isListening
                              ? [BoxShadow(
                                  color: primary.withValues(alpha: 0.5),
                                  blurRadius: 12,
                                  spreadRadius: 2,
                                )]
                              : [],
                        ),
                        child: Icon(
                          voice.isListening ? Icons.stop : Icons.mic,
                          color: Colors.white,
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
    _pulseCtrl.dispose();
    _errorSub?.cancel();
    _textCtrl.dispose();
    _scroll.dispose();
    super.dispose();
  }
}
