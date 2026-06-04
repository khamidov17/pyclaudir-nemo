import 'dart:async';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import '../models/chat_session.dart';
import '../widgets/panic_button.dart';
import '../services/chat_storage.dart';
import '../services/nemo_service.dart';
import '../services/voice_service.dart';
import '../services/wake_word_service.dart';
import 'chat_screen.dart';
import 'settings_screen.dart';
import 'voice_chat_screen.dart';

class ChatListScreen extends StatefulWidget {
  const ChatListScreen({super.key});
  @override
  State<ChatListScreen> createState() => _ChatListScreenState();
}

class _ChatListScreenState extends State<ChatListScreen> {
  List<ChatSession> _sessions = [];

  @override
  void initState() {
    super.initState();
    _load();
    _initVoice();
  }

  Future<void> _initVoice() async {
    final voice = context.read<VoiceService>();
    final nemo = context.read<NemoService>();
    final wake = context.read<WakeWordService>();

    await voice.init();
    await nemo.connect();

    // Edge TTS audio → play automatically
    nemo.audioB64.listen((b64) => voice.playAudio(b64));

    // Wake word → auto open voice chat
    wake.onWakeWord = () async {
      if (!mounted) return;
      await wake.stop();
      final host = _extractHost(nemo.serverUrl);
      await Navigator.push(context, PageRouteBuilder(
        pageBuilder: (_, __, ___) => VoiceChatScreen(serverHost: host, autoStart: true),
        transitionDuration: const Duration(milliseconds: 150),
        transitionsBuilder: (_, a, __, c) => FadeTransition(opacity: a, child: c),
      ));
      if (mounted) await wake.start();
    };

    await Future.delayed(const Duration(milliseconds: 600));
    await wake.start();
  }

  String _extractHost(String url) {
    try { return Uri.parse(url).host; } catch (_) {
      return url.replaceAll(RegExp(r'^wss?://'), '').split(':').first;
    }
  }

  Future<void> _load() async {
    final sessions = await ChatStorage.getSessions();
    setState(() => _sessions = sessions);
  }

  Future<void> _newChat() async {
    final id = 'chat_${DateTime.now().millisecondsSinceEpoch}';
    final session = ChatSession(
      id: id,
      title: 'New conversation',
      createdAt: DateTime.now(),
      updatedAt: DateTime.now(),
    );
    await ChatStorage.saveSession(session);
    if (!mounted) return;
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ChatScreen(session: session)),
    );
    _load();
  }

  Future<void> _openChat(ChatSession session) async {
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ChatScreen(session: session)),
    );
    _load();
  }

  Future<void> _deleteChat(ChatSession session) async {
    await ChatStorage.deleteSession(session.id);
    _load();
  }

  String _formatTime(DateTime t) {
    final now = DateTime.now();
    if (t.day == now.day && t.month == now.month && t.year == now.year) {
      return DateFormat('HH:mm').format(t);
    }
    if (now.difference(t).inDays < 7) {
      return DateFormat('EEE').format(t);
    }
    return DateFormat('dd/MM').format(t);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F0F1A),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0F0F1A),
        elevation: 0,
        title: Row(children: [
          Container(
            width: 32, height: 32,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: const LinearGradient(
                colors: [Color(0xFF7C3AED), Color(0xFF3B82F6)],
              ),
            ),
            child: const Center(
              child: Text('N', style: TextStyle(color: Colors.white,
                  fontWeight: FontWeight.bold, fontSize: 16)),
            ),
          ),
          const SizedBox(width: 10),
          const Text('Nemo', style: TextStyle(fontWeight: FontWeight.bold)),
        ]),
        actions: [
          const PanicButton(),
          const SizedBox(width: 4),
          IconButton(
            icon: const Icon(Icons.settings_outlined),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const SettingsScreen()),
            ).then((_) => _load()),
          ),
        ],
      ),
      body: _sessions.isEmpty
          ? Center(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                const Text('👋', style: TextStyle(fontSize: 48)),
                const SizedBox(height: 16),
                const Text('No conversations yet',
                    style: TextStyle(color: Colors.white38, fontSize: 16)),
                const SizedBox(height: 24),
                FilledButton.icon(
                  onPressed: _newChat,
                  icon: const Icon(Icons.add),
                  label: const Text('Start a conversation'),
                ),
              ]),
            )
          : ListView.separated(
              itemCount: _sessions.length,
              separatorBuilder: (_, __) => const Divider(
                  color: Color(0xFF1E1E2E), height: 1),
              itemBuilder: (_, i) {
                final s = _sessions[i];
                return Dismissible(
                  key: Key(s.id),
                  direction: DismissDirection.endToStart,
                  background: Container(
                    alignment: Alignment.centerRight,
                    padding: const EdgeInsets.only(right: 20),
                    color: Colors.red.shade900,
                    child: const Icon(Icons.delete_outline, color: Colors.white),
                  ),
                  onDismissed: (_) => _deleteChat(s),
                  child: ListTile(
                    contentPadding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 6),
                    leading: Container(
                      width: 44, height: 44,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: const Color(0xFF1E1E2E),
                        border: Border.all(
                            color: const Color(0xFF7C3AED).withOpacity(0.3)),
                      ),
                      child: const Icon(Icons.chat_bubble_outline,
                          color: Color(0xFF7C3AED), size: 20),
                    ),
                    title: Text(
                      s.title,
                      style: const TextStyle(color: Colors.white,
                          fontWeight: FontWeight.w500),
                      maxLines: 1, overflow: TextOverflow.ellipsis,
                    ),
                    subtitle: s.lastMessage.isNotEmpty
                        ? Text(
                            s.lastMessage,
                            style: const TextStyle(color: Colors.white38,
                                fontSize: 13),
                            maxLines: 1, overflow: TextOverflow.ellipsis,
                          )
                        : null,
                    trailing: Text(
                      _formatTime(s.updatedAt),
                      style: const TextStyle(color: Colors.white38,
                          fontSize: 12),
                    ),
                    onTap: () => _openChat(s),
                  ),
                );
              },
            ),
      floatingActionButton: FloatingActionButton(
        onPressed: _newChat,
        backgroundColor: const Color(0xFF7C3AED),
        child: const Icon(Icons.add, color: Colors.white),
      ),
    );
  }
}
