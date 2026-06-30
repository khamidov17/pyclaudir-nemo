import 'package:sqflite/sqflite.dart';
import 'package:path/path.dart' as p;
import '../models/chat_session.dart';
import '../models/message.dart';

class ChatStorage {
  static Database? _db;

  static Future<Database> get db async {
    _db ??= await _open();
    return _db!;
  }

  static Future<Database> _open() async {
    final dir = await getDatabasesPath();
    return openDatabase(
      p.join(dir, 'nemo_chats.db'),
      version: 1,
      onUpgrade: (db, oldVersion, newVersion) async {
        // Migrations go here when version is bumped — keep this handler even if empty.
      },
      onCreate: (db, _) async {
        await db.execute('''
          CREATE TABLE sessions(
            id TEXT PRIMARY KEY,
            title TEXT,
            created_at INTEGER,
            updated_at INTEGER,
            last_message TEXT
          )''');
        await db.execute('''
          CREATE TABLE messages(
            id TEXT PRIMARY KEY,
            session_id TEXT,
            text TEXT,
            sender TEXT,
            time INTEGER,
            type TEXT,
            media_path TEXT,
            media_name TEXT
          )''');
      },
    );
  }

  // ── Sessions ────────────────────────────────────────────────
  static Future<List<ChatSession>> getSessions() async {
    final rows = await (await db).query('sessions',
        orderBy: 'updated_at DESC');
    return rows.map(ChatSession.fromMap).toList();
  }

  static Future<void> saveSession(ChatSession s) async {
    await (await db).insert('sessions', s.toMap(),
        conflictAlgorithm: ConflictAlgorithm.replace);
  }

  static Future<void> deleteSession(String id) async {
    final d = await db;
    await d.transaction((txn) async {
      await txn.delete('sessions', where: 'id=?', whereArgs: [id]);
      await txn.delete('messages', where: 'session_id=?', whereArgs: [id]);
    });
  }

  static Future<void> updateSessionMeta(String id, String lastMessage) async {
    await (await db).update(
      'sessions',
      {'updated_at': DateTime.now().millisecondsSinceEpoch, 'last_message': lastMessage},
      where: 'id=?', whereArgs: [id],
    );
  }

  // ── Messages ────────────────────────────────────────────────
  static Future<List<Message>> getMessages(String sessionId) async {
    final rows = await (await db).query('messages',
        where: 'session_id=?', whereArgs: [sessionId], orderBy: 'time ASC');
    return rows.map(Message.fromMap).toList();
  }

  static Future<void> saveMessage(Message m) async {
    final d = await db;
    final snippet = m.text.length > 60 ? '${m.text.substring(0, 60)}…' : m.text;
    await d.transaction((txn) async {
      await txn.insert('messages', m.toMap(),
          conflictAlgorithm: ConflictAlgorithm.replace);
      await txn.update(
        'sessions',
        {'updated_at': DateTime.now().millisecondsSinceEpoch, 'last_message': snippet},
        where: 'id=?', whereArgs: [m.sessionId],
      );
    });
  }

  static Future<void> deleteMessage(String id) async {
    await (await db).delete('messages', where: 'id=?', whereArgs: [id]);
  }
}
