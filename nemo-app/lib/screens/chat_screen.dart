import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:provider/provider.dart';
import 'package:uuid/uuid.dart';
import '../app_version.dart';
import '../models/chat_session.dart';
import '../models/message.dart';
import '../services/chat_storage.dart';
import '../services/nemo_service.dart';
import '../services/recording_service.dart';
import '../services/voice_service.dart';
import '../theme.dart';
import '../widgets/message_bubble.dart';
import 'voice_chat_screen.dart';

const _uuid = Uuid();

class ChatScreen extends StatefulWidget {
  final ChatSession session;
  const ChatScreen({super.key, required this.session});
  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final TextEditingController _ctrl = TextEditingController();
  final ScrollController _scroll = ScrollController();
  List<Message> _messages = [];
  bool _thinking = false;
  late StreamSubscription _msgSub;
  late StreamSubscription _audioSub;
  late StreamSubscription _errorSub;
  late RecordingService _rec;
  final _imagePicker = ImagePicker();

  @override
  void initState() {
    super.initState();
    _rec = RecordingService();
    _loadMessages();
    final nemo = context.read<NemoService>();
    _msgSub = nemo.messages.listen(_onNemoReply);
    _audioSub = nemo.audioB64.listen((b64) {
      context.read<VoiceService>().playAudio(b64);
    });
    _errorSub = nemo.errors.listen((message) {
      if (!mounted) return;
      setState(() => _thinking = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(message)),
      );
    });
  }

  Future<void> _loadMessages() async {
    final msgs = await ChatStorage.getMessages(widget.session.id);
    setState(() => _messages = msgs);
    _scrollDown();
  }

  void _onNemoReply(String text) {
    _addMessage(text, Sender.nemo);
    setState(() => _thinking = false);
  }

  void _addMessage(
    String text,
    Sender sender, {
    MessageType type = MessageType.text,
    String? mediaPath,
    String? mediaName,
  }) {
    final m = Message(
      id: _uuid.v4(),
      sessionId: widget.session.id,
      text: text,
      sender: sender,
      time: DateTime.now(),
      type: type,
      mediaPath: mediaPath,
      mediaName: mediaName,
    );
    setState(() => _messages.add(m));
    ChatStorage.saveMessage(m);
    // Auto-rename chat from first user message
    if (sender == Sender.user && _messages.length == 1 && text.isNotEmpty) {
      final title = text.length > 40 ? '${text.substring(0, 40)}…' : text;
      ChatStorage.saveSession(ChatSession(
        id: widget.session.id,
        title: title,
        createdAt: widget.session.createdAt,
        updatedAt: DateTime.now(),
      ));
    }
    _scrollDown();
  }

  void _scrollDown() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(_scroll.position.maxScrollExtent,
            duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      }
    });
  }

  Future<void> _send([String? override]) async {
    final text = (override ?? _ctrl.text).trim();
    if (text.isEmpty) return;
    _ctrl.clear();
    _addMessage(text, Sender.user);
    setState(() => _thinking = true);
    final sent = await context.read<NemoService>().send(text);
    if (!sent && mounted) setState(() => _thinking = false);
  }

  Future<void> _sendImage() async {
    final picked = await _imagePicker.pickImage(
        source: ImageSource.gallery, imageQuality: 85);
    if (picked == null) return;
    final bytes = await picked.readAsBytes();
    final b64 = base64Encode(bytes);
    // Save to local storage
    final dir = await getApplicationDocumentsDirectory();
    final dest = '${dir.path}/${_uuid.v4()}.jpg';
    await File(dest).writeAsBytes(bytes);
    _addMessage('', Sender.user, type: MessageType.image, mediaPath: dest);

    // Ask Nemo about the image
    setState(() => _thinking = true);
    final sent = await context.read<NemoService>().sendWithMedia(
        '[Image attached — please describe and analyze it]', b64, 'image/jpeg');
    if (!sent && mounted) setState(() => _thinking = false);
  }

  Future<void> _takePhoto() async {
    final picked = await _imagePicker.pickImage(
        source: ImageSource.camera, imageQuality: 85);
    if (picked == null) return;
    final bytes = await picked.readAsBytes();
    final b64 = base64Encode(bytes);
    final dir = await getApplicationDocumentsDirectory();
    final dest = '${dir.path}/${_uuid.v4()}.jpg';
    await File(dest).writeAsBytes(bytes);
    _addMessage('', Sender.user, type: MessageType.image, mediaPath: dest);
    setState(() => _thinking = true);
    final sent = await context
        .read<NemoService>()
        .sendWithMedia('[Photo taken — please describe it]', b64, 'image/jpeg');
    if (!sent && mounted) setState(() => _thinking = false);
  }

  Future<void> _sendFile() async {
    // PDF/file sending coming in next update
    ScaffoldMessenger.of(context)
        .showSnackBar(const SnackBar(content: Text('PDF sending coming soon')));
  }

  Future<void> _toggleRecording() async {
    if (_rec.isRecording) {
      final result = await _rec.stopAndTranscribe();
      if (result == null) return;
      final dur = result.duration.inSeconds;
      final transcript =
          result.transcript ?? '[No transcript — add Groq API key]';
      final summary =
          'Recorded ${dur}s conversation.\n\nTranscript:\n$transcript';
      _addMessage(summary, Sender.user, type: MessageType.recording);
      // Save to Nemo memory automatically
      setState(() => _thinking = true);
      final sent = await context.read<NemoService>().send(
          'I just recorded a ${dur}s conversation. Please save this to memory '
          'and summarize what was discussed:\n\n$transcript');
      if (!sent && mounted) setState(() => _thinking = false);
    } else {
      await _rec.startRecording();
      setState(() {});
    }
  }

  void _showAttachMenu() {
    showModalBottomSheet(
      context: context,
      backgroundColor: NemoColors.surface,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (_) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          _menuItem(Icons.photo_library, 'Photo from gallery', _sendImage),
          _menuItem(Icons.camera_alt, 'Take photo', _takePhoto),
          _menuItem(Icons.insert_drive_file, 'Send PDF/document', _sendFile),
          _menuItem(
            _rec.isRecording ? Icons.stop_circle : Icons.mic,
            _rec.isRecording ? 'Stop recording' : 'Record conversation',
            () {
              Navigator.pop(context);
              _toggleRecording();
            },
            color: _rec.isRecording ? Colors.red : null,
          ),
        ]),
      ),
    );
  }

  Widget _menuItem(IconData icon, String label, VoidCallback onTap,
      {Color? color}) {
    return ListTile(
      leading: Icon(icon, color: color ?? NemoColors.accent),
      title: Text(label, style: TextStyle(color: color ?? NemoColors.text)),
      onTap: () {
        Navigator.pop(context);
        onTap();
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final nemo = context.watch<NemoService>();

    return Scaffold(
      appBar: AppBar(
        title: Row(children: [
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: nemo.isConnected ? NemoColors.accent : NemoColors.danger,
            ),
          ),
          const SizedBox(width: 8),
          const Text('Nemo $nemoVersionLabel',
              style: TextStyle(fontWeight: FontWeight.w600)),
          if (_thinking) ...[
            const SizedBox(width: 8),
            const SizedBox(
                width: 12,
                height: 12,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: NemoColors.textDim)),
          ],
          if (_rec.isRecording) ...[
            const SizedBox(width: 8),
            const Icon(Icons.fiber_manual_record,
                color: NemoColors.danger, size: 10),
            const SizedBox(width: 4),
            Text('${_rec.elapsedSeconds}s',
                style: const TextStyle(color: NemoColors.danger, fontSize: 12)),
          ],
        ]),
        actions: [
          IconButton(
            icon: const Icon(Icons.graphic_eq, color: NemoColors.accent),
            tooltip: 'Talk to Nemo',
            onPressed: () {
              Navigator.push(
                  context,
                  MaterialPageRoute(
                      builder: (_) => const VoiceChatScreen(autoStart: true)));
            },
          ),
        ],
      ),
      body: Column(children: [
        Expanded(
          child: _messages.isEmpty
              ? Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                  const Icon(Icons.forum_outlined,
                      color: NemoColors.textFaint, size: 40),
                  const SizedBox(height: 16),
                  const Text('Say anything',
                      style:
                          TextStyle(color: NemoColors.textDim, fontSize: 16)),
                  const SizedBox(height: 6),
                  const Text('Text, photos, PDFs — or tap the wave to talk',
                      style:
                          TextStyle(color: NemoColors.textFaint, fontSize: 13)),
                ]))
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  itemCount: _messages.length,
                  itemBuilder: (_, i) => MessageBubble(message: _messages[i]),
                ),
        ),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(8, 6, 8, 6),
            child: Row(children: [
              IconButton(
                icon: const Icon(Icons.add_circle_outline,
                    color: NemoColors.accent),
                onPressed: _showAttachMenu,
              ),
              Expanded(
                child: TextField(
                  controller: _ctrl,
                  style: const TextStyle(color: NemoColors.text),
                  maxLines: null,
                  decoration: InputDecoration(
                    hintText: 'Message Nemo…',
                    filled: true,
                    fillColor: NemoColors.surface,
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(24),
                      borderSide: BorderSide.none,
                    ),
                    enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(24),
                      borderSide: BorderSide.none,
                    ),
                    contentPadding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 10),
                  ),
                  onSubmitted: (_) => _send(),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.send_rounded),
                color: NemoColors.accent,
                onPressed: _send,
              ),
            ]),
          ),
        ),
      ]),
    );
  }

  @override
  void dispose() {
    _msgSub.cancel();
    _audioSub.cancel();
    _errorSub.cancel();
    _ctrl.dispose();
    _scroll.dispose();
    _rec.dispose();
    super.dispose();
  }
}
