import 'package:flutter/services.dart';

/// Wraps the native message-awareness channel — the captured-notification
/// buffer + the notification-access permission state.
class MessageAwareness {
  static const _ch = MethodChannel('com.avazbek.nemo_app/messages');

  /// Recent captured messages as a JSON array string (the server parses it).
  static Future<String> getMessages() async =>
      await _ch.invokeMethod<String>('getMessages') ?? '[]';

  /// Whether the user has granted notification access to Nemo.
  static Future<bool> isAccessGranted() async {
    try {
      return await _ch.invokeMethod<bool>('isAccessGranted') ?? false;
    } catch (_) {
      return false;
    }
  }

  /// Deep-link to the system notification-access settings to grant it.
  static Future<void> openAccessSettings() =>
      _ch.invokeMethod<void>('openAccessSettings');
}
