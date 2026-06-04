class ChatSession {
  final String id;
  final String title;
  final DateTime createdAt;
  final DateTime updatedAt;
  final String lastMessage;

  const ChatSession({
    required this.id,
    required this.title,
    required this.createdAt,
    required this.updatedAt,
    this.lastMessage = '',
  });

  Map<String, dynamic> toMap() => {
    'id': id,
    'title': title,
    'created_at': createdAt.millisecondsSinceEpoch,
    'updated_at': updatedAt.millisecondsSinceEpoch,
    'last_message': lastMessage,
  };

  factory ChatSession.fromMap(Map<String, dynamic> m) => ChatSession(
    id: m['id'] as String,
    title: m['title'] as String,
    createdAt: DateTime.fromMillisecondsSinceEpoch(m['created_at'] as int),
    updatedAt: DateTime.fromMillisecondsSinceEpoch(m['updated_at'] as int),
    lastMessage: m['last_message'] as String? ?? '',
  );
}
