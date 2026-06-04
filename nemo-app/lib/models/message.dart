enum Sender { user, nemo }
enum MessageType { text, image, file, audio, recording }

class Message {
  final String id;
  final String sessionId;
  final String text;
  final Sender sender;
  final DateTime time;
  final MessageType type;
  final String? mediaPath;   // local file path for images/files
  final String? mediaName;   // display name for files
  final String? mediaB64;    // base64 for sending (not stored)

  const Message({
    required this.id,
    required this.sessionId,
    required this.text,
    required this.sender,
    required this.time,
    this.type = MessageType.text,
    this.mediaPath,
    this.mediaName,
    this.mediaB64,
  });

  Map<String, dynamic> toMap() => {
    'id': id,
    'session_id': sessionId,
    'text': text,
    'sender': sender.name,
    'time': time.millisecondsSinceEpoch,
    'type': type.name,
    'media_path': mediaPath,
    'media_name': mediaName,
  };

  factory Message.fromMap(Map<String, dynamic> m) => Message(
    id: m['id'] as String,
    sessionId: m['session_id'] as String,
    text: m['text'] as String,
    sender: Sender.values.firstWhere((s) => s.name == m['sender']),
    time: DateTime.fromMillisecondsSinceEpoch(m['time'] as int),
    type: MessageType.values.firstWhere(
      (t) => t.name == m['type'],
      orElse: () => MessageType.text,
    ),
    mediaPath: m['media_path'] as String?,
    mediaName: m['media_name'] as String?,
  );
}
