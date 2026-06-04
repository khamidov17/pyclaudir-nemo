import 'dart:async';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import '../app_version.dart';
import '../models/chat_session.dart';
import '../widgets/panic_button.dart';
import '../services/chat_storage.dart';
import '../services/nemo_service.dart';
import '../services/update_service.dart';
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
  StreamSubscription? _errorSub;
  bool _updatePromptShown = false;

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
    _errorSub = nemo.errors.listen((message) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(message)),
      );
    });
    await nemo.connect();
    await context.read<UpdateService>().checkForUpdate(nemo.serverUrl);

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

  Future<void> _installUpdate() async {
    final url = context.read<NemoService>().serverUrl;
    await context.read<UpdateService>().downloadAndInstall(url);
  }

  void _maybePromptUpdate(UpdateService updater) {
    if (_updatePromptShown || !updater.updateAvailable || updater.isDownloading) {
      return;
    }
    _updatePromptShown = true;
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      final install = await showDialog<bool>(
        context: context,
        builder: (_) => AlertDialog(
          backgroundColor: const Color(0xFF1E1E2E),
          title: Text('Nemo v${updater.serverVersion} available',
              style: const TextStyle(color: Colors.white)),
          content: const Text(
            'Install the latest Nemo APK now?',
            style: TextStyle(color: Colors.white70),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Later'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Update'),
            ),
          ],
        ),
      );
      if (install == true && mounted) await _installUpdate();
    });
  }

  Widget _updateBanner() {
    return Consumer<UpdateService>(
      builder: (_, updater, __) {
        _maybePromptUpdate(updater);
        if (!updater.updateAvailable && !updater.isDownloading) {
          return const SizedBox.shrink();
        }
        return Container(
          color: const Color(0xFF7C3AED),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          child: updater.isDownloading
              ? Row(children: [
                  const SizedBox(width: 14, height: 14,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Colors.white)),
                  const SizedBox(width: 10),
                  Text(
                    'Downloading ${(updater.downloadProgress * 100).toStringAsFixed(0)}%',
                    style: const TextStyle(color: Colors.white, fontSize: 13),
                  ),
                ])
              : Row(children: [
                  const Icon(Icons.system_update, color: Colors.white, size: 18),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text('Nemo v${updater.serverVersion} available',
                        style: const TextStyle(color: Colors.white, fontSize: 13)),
                  ),
                  TextButton(
                    onPressed: _installUpdate,
                    child: const Text('Update',
                        style: TextStyle(color: Colors.white,
                            fontWeight: FontWeight.bold)),
                  ),
                ]),
        );
      },
    );
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
          const Text('Nemo $nemoVersionLabel',
              style: TextStyle(fontWeight: FontWeight.bold)),
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
      body: Column(children: [
        _updateBanner(),
        Expanded(
          child: _sessions.isEmpty
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
                                color: const Color(0xFF7C3AED)
                                    .withValues(alpha: 0.3)),
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
        ),
      ]),
      floatingActionButton: FloatingActionButton(
        onPressed: _newChat,
        backgroundColor: const Color(0xFF7C3AED),
        child: const Icon(Icons.add, color: Colors.white),
      ),
    );
  }

  @override
  void dispose() {
    _errorSub?.cancel();
    super.dispose();
  }
}
