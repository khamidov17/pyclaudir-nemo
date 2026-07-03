import 'dart:async';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import '../models/chat_session.dart';
import '../theme.dart';
import '../widgets/panic_button.dart';
import '../widgets/voice_orb.dart';
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

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // Wire update-prompt listener here so it's never called from build().
    final updater = context.read<UpdateService>();
    updater.addListener(_onUpdateChanged);
  }

  void _onUpdateChanged() {
    _maybePromptUpdate(context.read<UpdateService>());
  }

  @override
  void dispose() {
    context.read<UpdateService>().removeListener(_onUpdateChanged);
    _errorSub?.cancel();
    super.dispose();
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
    // Update check runs once at launch (main.dart, 5s delay). No repeat here.

    // TTS playback is wired app-globally in main.dart so proactive audio
    // (reminders/briefings) plays on any screen — not duplicated here.

    // Wake word handling lives in main.dart (VoiceSessionController) so
    // "hey nemo" works hands-free from ANY app — no UI navigation here.
    await Future.delayed(const Duration(milliseconds: 600));
    await wake.start();
  }

  Future<void> _startVoice() async {
    // The session is owned by VoiceSessionController; this screen is a view.
    await Navigator.push(
        context,
        PageRouteBuilder(
          pageBuilder: (_, __, ___) => const VoiceChatScreen(autoStart: true),
          transitionDuration: const Duration(milliseconds: 250),
          transitionsBuilder: (_, a, __, c) =>
              FadeTransition(opacity: a, child: c),
        ));
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
    final updater = context.read<UpdateService>();
    final url = context.read<NemoService>().serverUrl;
    try {
      // Hash-verified in-app download + install.
      await updater.downloadAndInstall(url);
    } catch (e) {
      if (!mounted) return;
      final fallback = await showDialog<bool>(
        context: context,
        builder: (_) => AlertDialog(
          title: const Text('Verified install failed'),
          content: Text(
            '$e\n\nOpen the download in the browser instead? '
            '(no hash verification on that path)',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Use browser'),
            ),
          ],
        ),
      );
      if (fallback == true && mounted) {
        try {
          await updater.openDownloadInBrowser(url);
        } catch (e2) {
          if (mounted) {
            ScaffoldMessenger.of(context)
                .showSnackBar(SnackBar(content: Text('$e2')));
          }
        }
      }
    }
  }

  void _maybePromptUpdate(UpdateService updater) {
    if (_updatePromptShown ||
        !updater.updateAvailable ||
        updater.isDownloading) {
      return;
    }
    _updatePromptShown = true;
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      final install = await showDialog<bool>(
        context: context,
        builder: (_) => AlertDialog(
          title: Text('Nemo v${updater.serverVersion} available'),
          content: const Text('Install the latest Nemo APK now?'),
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
        if (!updater.updateAvailable && !updater.isDownloading) {
          return const SizedBox.shrink();
        }
        return Container(
          margin: const EdgeInsets.fromLTRB(16, 8, 16, 0),
          decoration: BoxDecoration(
            color: NemoColors.accent.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: NemoColors.accent.withValues(alpha: 0.4)),
          ),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          child: updater.isDownloading
              ? Row(children: [
                  const SizedBox(
                      width: 14,
                      height: 14,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: NemoColors.accent)),
                  const SizedBox(width: 10),
                  Text(
                    'Downloading ${(updater.downloadProgress * 100).toStringAsFixed(0)}%',
                    style:
                        const TextStyle(color: NemoColors.text, fontSize: 13),
                  ),
                ])
              : Row(children: [
                  const Icon(Icons.system_update,
                      color: NemoColors.accent, size: 18),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text('Nemo v${updater.serverVersion} available',
                        style: const TextStyle(
                            color: NemoColors.text, fontSize: 13)),
                  ),
                  TextButton(
                    onPressed: _installUpdate,
                    child: const Text('Update',
                        style: TextStyle(fontWeight: FontWeight.bold)),
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

  void _openChats() {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: NemoColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(22)),
      ),
      builder: (_) => _ChatsSheet(
        sessions: _sessions,
        formatTime: _formatTime,
        onOpen: (s) {
          Navigator.pop(context);
          _openChat(s);
        },
        onDelete: _deleteChat,
        onNew: () {
          Navigator.pop(context);
          _newChat();
        },
      ),
    ).then((_) => _load());
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('NEMO',
            style: TextStyle(letterSpacing: 4, fontWeight: FontWeight.w700)),
        actions: [
          const PanicButton(),
          const SizedBox(width: 4),
          IconButton(
            icon:
                const Icon(Icons.settings_outlined, color: NemoColors.textDim),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const SettingsScreen()),
            ).then((_) => _load()),
          ),
        ],
      ),
      body: Column(children: [
        _updateBanner(),
        // Orb + labels centered in all the space between the banner and the
        // bottom button — dead center of the screen.
        Expanded(
          child: Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                VoiceOrb(state: OrbState.idle, size: 250, onTap: _startVoice),
                const SizedBox(height: 44),
                const Text(
                  'Tap to talk to Nemo',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: NemoColors.text,
                    fontSize: 21,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.4,
                  ),
                ),
                const SizedBox(height: 8),
                const Text(
                  'or just say "Hey Nemo"',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: NemoColors.textFaint, fontSize: 14),
                ),
              ],
            ),
          ),
        ),
        _chatsButton(),
        const SizedBox(height: 24),
      ]),
    );
  }

  Widget _chatsButton() {
    final count = _sessions.length;
    return TextButton.icon(
      onPressed: _openChats,
      icon:
          const Icon(Icons.forum_outlined, size: 18, color: NemoColors.textDim),
      label: Text(
        count == 0 ? 'Text chats' : 'Text chats ($count)',
        style: const TextStyle(color: NemoColors.textDim, fontSize: 14),
      ),
    );
  }

}

/// Secondary, tucked-away list of text conversations.
class _ChatsSheet extends StatelessWidget {
  final List<ChatSession> sessions;
  final String Function(DateTime) formatTime;
  final void Function(ChatSession) onOpen;
  final Future<void> Function(ChatSession) onDelete;
  final VoidCallback onNew;

  const _ChatsSheet({
    required this.sessions,
    required this.formatTime,
    required this.onOpen,
    required this.onDelete,
    required this.onNew,
  });

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const SizedBox(height: 12),
          Container(
            width: 40,
            height: 4,
            decoration: BoxDecoration(
              color: NemoColors.border,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 16, 12, 4),
            child: Row(children: [
              const Text('Text chats',
                  style: TextStyle(
                      color: NemoColors.text,
                      fontSize: 18,
                      fontWeight: FontWeight.w600)),
              const Spacer(),
              TextButton.icon(
                onPressed: onNew,
                icon: const Icon(Icons.add, size: 18),
                label: const Text('New'),
              ),
            ]),
          ),
          Flexible(
            child: sessions.isEmpty
                ? const Padding(
                    padding: EdgeInsets.symmetric(vertical: 48),
                    child: Text('No text chats yet',
                        style: TextStyle(color: NemoColors.textFaint)),
                  )
                : ListView.separated(
                    shrinkWrap: true,
                    padding: const EdgeInsets.only(bottom: 12),
                    itemCount: sessions.length,
                    separatorBuilder: (_, __) =>
                        const Divider(color: NemoColors.border, height: 1),
                    itemBuilder: (_, i) => _tile(sessions[i]),
                  ),
          ),
        ],
      ),
    );
  }

  Widget _tile(ChatSession s) {
    return Dismissible(
      key: Key(s.id),
      direction: DismissDirection.endToStart,
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.only(right: 20),
        color: NemoColors.danger.withValues(alpha: 0.8),
        child: const Icon(Icons.delete_outline, color: Colors.white),
      ),
      onDismissed: (_) => onDelete(s),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
        leading: Container(
          width: 42,
          height: 42,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: NemoColors.bg,
            border: Border.all(color: NemoColors.accent.withValues(alpha: 0.3)),
          ),
          child: const Icon(Icons.chat_bubble_outline,
              color: NemoColors.accent, size: 18),
        ),
        title: Text(s.title,
            style: const TextStyle(
                color: NemoColors.text, fontWeight: FontWeight.w500),
            maxLines: 1,
            overflow: TextOverflow.ellipsis),
        subtitle: s.lastMessage.isNotEmpty
            ? Text(s.lastMessage,
                style:
                    const TextStyle(color: NemoColors.textFaint, fontSize: 13),
                maxLines: 1,
                overflow: TextOverflow.ellipsis)
            : null,
        trailing: Text(formatTime(s.updatedAt),
            style: const TextStyle(color: NemoColors.textFaint, fontSize: 12)),
        onTap: () => onOpen(s),
      ),
    );
  }
}
