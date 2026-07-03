import 'package:flutter/foundation.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';

/// Background foreground service that keeps Nemo alive when the app is
/// minimized or the screen is off — so "Hey Nemo" can be heard AND scheduled
/// reminders/briefings can be spoken on time even with the phone pocketed.
///
/// Neither the wake word nor playback runs here; this service only prevents
/// Android from killing the process. Wake word lives in WakeWordService and
/// TTS playback in VoiceService, both on the main isolate.
class BackgroundWakeWordService {
  static bool _initialized = false;

  /// Must be called before start() — idempotent.
  static Future<void> init() async {
    if (_initialized) return;
    _initialized = true;

    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'nemo_wake_word',
        channelName: 'Nemo',
        channelDescription: 'Keeps Nemo ready for voice and reminders',
        channelImportance: NotificationChannelImportance.LOW,
        priority: NotificationPriority.LOW,
      ),
      iosNotificationOptions: const IOSNotificationOptions(
        showNotification: false,
        playSound: false,
      ),
      foregroundTaskOptions: ForegroundTaskOptions(
        eventAction: ForegroundTaskEventAction.repeat(60000),
        autoRunOnBoot: true,
        allowWakeLock: true,
        allowWifiLock: true,
      ),
    );
  }

  /// Start the foreground service (shows persistent notification).
  /// [statusText] describes what Nemo is doing — pass the wake-word line when
  /// it's enabled, else a generic ready line.
  static Future<void> start({
    String statusText = 'Active — ready for voice and reminders',
  }) async {
    await init();
    if (await FlutterForegroundTask.isRunningService) return;

    await FlutterForegroundTask.startService(
      serviceId: 256,
      notificationTitle: 'Nemo',
      notificationText: statusText,
      notificationIcon: null,
      callback: _noopCallback,
    );

    debugPrint('BackgroundWakeWordService: started ($statusText)');
  }

  static Future<void> stop() async {
    await FlutterForegroundTask.stopService();
  }

  static Future<bool> get isRunning =>
      FlutterForegroundTask.isRunningService;
}

/// Minimal no-op task handler — the actual wake word logic lives in
/// WakeWordService on the main isolate. This just keeps the process alive.
@pragma('vm:entry-point')
void _noopCallback() {
  FlutterForegroundTask.setTaskHandler(_KeepAliveHandler());
}

class _KeepAliveHandler extends TaskHandler {
  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {}

  @override
  Future<void> onRepeatEvent(DateTime timestamp) async {
    // Keep-alive tick only — don't overwrite the status text set at start
    // (which reflects whether wake word is on).
  }

  @override
  Future<void> onDestroy(DateTime timestamp) async {}
}
