import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:provider/provider.dart';
import 'package:uuid/uuid.dart';
import '../models/chat_session.dart';
import '../models/message.dart';
import '../services/chat_storage.dart';
import '../services/nemo_service.dart';
import '../services/recording_service.dart';
import '../services/voice_service.dart';
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

  void _addMessage(String text, Sender sender, {
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
    await context.read<NemoService>().send(text);
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
    _addMessage('', Sender.user,
        type: MessageType.image, mediaPath: dest);

    // Ask Nemo about the image
    setState(() => _thinking = true);
    await context.read<NemoService>().sendWithMedia(
      '[Image attached — please describe and analyze it]', b64, 'image/jpeg');
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
    _addMessage('', Sender.user,
        type: MessageType.image, mediaPath: dest);
    setState(() => _thinking = true);
    await context.read<NemoService>().sendWithMedia(
      '[Photo taken — please describe it]', b64, 'image/jpeg');
  }

  Future<void> _sendFile() async {
    // PDF/file sending coming in next update
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('PDF sending coming soon')));
  }

  Future<void> _toggleRecording() async {
    if (_rec.isRecording) {
      final result = await _rec.stopAndTranscribe();
      if (result == null) return;
      final dur = result.duration.inSeconds;
      final transcript = result.transcript ?? '[No transcript — add Groq API key]';
      final summary = 'Recorded ${dur}s conversation.\n\nTranscript:\n$transcript';
      _addMessage(summary, Sender.user,
          type: MessageType.recording);
      // Save to Nemo memory automatically
      setState(() => _thinking = true);
      await context.read<NemoService>().send(
        'I just recorded a ${dur}s conversation. Please save this to memory '
        'and summarize what was discussed:\n\n$transcript');
    } else {
      await _rec.startRecording();
      setState(() {});
    }
  }

  void _showAttachMenu() {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF1E1E2E),
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
      builder: (_) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          _menuItem(Icons.photo_library, 'Photo from gallery', _sendImage),
          _menuItem(Icons.camera_alt, 'Take photo', _takePhoto),
          _menuItem(Icons.insert_drive_file, 'Send PDF/document', _sendFile),
          _menuItem(
            _rec.isRecording ? Icons.stop_circle : Icons.mic,
            _rec.isRecording ? 'Stop recording' : 'Record conversation',
            () { Navigator.pop(context); _toggleRecording(); },
            color: _rec.isRecording ? Colors.red : null,
          ),
        ]),
      ),
    );
  }

  Widget _menuItem(IconData icon, String label, VoidCallback onTap,
      {Color? color}) {
    return ListTile(
      leading: Icon(icon, color: color ?? const Color(0xFF7C3AED)),
      title: Text(label, style: TextStyle(color: color ?? Colors.white)),
      onTap: () { Navigator.pop(context); onTap(); },
    );
  }

  @override
  Widget build(BuildContext context) {
    final nemo = context.watch<NemoService>();

    return Scaffold(
      backgroundColor: const Color(0xFF0F0F1A),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0F0F1A),
        elevation: 0,
        title: Row(children: [
          Container(
            width: 8, height: 8,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: nemo.isConnected ? Colors.greenAccent : Colors.redAccent,
            ),
          ),
          const SizedBox(width: 8),
          const Text('Nemo', style: TextStyle(fontWeight: FontWeight.bold)),
          if (_thinking) ...[
            const SizedBox(width: 8),
            const SizedBox(width: 12, height: 12,
              child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white38)),
          ],
          if (_rec.isRecording) ...[
            const SizedBox(width: 8),
            const Icon(Icons.fiber_manual_record, color: Colors.red, size: 10),
            const SizedBox(width: 4),
            Text('${_rec.elapsedSeconds}s',
                style: const TextStyle(color: Colors.redAccent, fontSize: 12)),
          ],
        ]),
        actions: [
          IconButton(
            icon: const Icon(Icons.record_voice_over),
            onPressed: () {
              final host = _extractHost(nemo.serverUrl);
              Navigator.push(context, MaterialPageRoute(
                builder: (_) => VoiceChatScreen(serverHost: host, autoStart: true)));
            },
          ),
        ],
      ),
      body: Column(children: [
        Expanded(
          child: _messages.isEmpty
              ? Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
                  const Text('💬', style: TextStyle(fontSize: 40)),
                  const SizedBox(height: 12),
                  const Text('Say anything', style: TextStyle(
                      color: Colors.white38, fontSize: 15)),
                  const SizedBox(height: 6),
                  const Text('Send text, photos, PDFs or tap 🎙',
                      style: TextStyle(color: Colors.white24, fontSize: 13)),
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
                icon: const Icon(Icons.add_circle_outline, color: Color(0xFF7C3AED)),
                onPressed: _showAttachMenu,
              ),
              Expanded(
                child: TextField(
                  controller: _ctrl,
                  style: const TextStyle(color: Colors.white),
                  maxLines: null,
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
                        horizontal: 16, vertical: 10),
                  ),
                  onSubmitted: (_) => _send(),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.send_rounded),
                color: const Color(0xFF7C3AED),
                onPressed: _send,
              ),
            ]),
          ),
        ),
      ]),
    );
  }

  String _extractHost(String url) {
    try { return Uri.parse(url).host; } catch (_) {
      return url.replaceAll(RegExp(r'^wss?://'), '').split(':').first;
    }
  }

  @override
  void dispose() {
    _msgSub.cancel();
    _audioSub.cancel();
    _ctrl.dispose();
    _scroll.dispose();
    _rec.dispose();
    super.dispose();
  }
}
