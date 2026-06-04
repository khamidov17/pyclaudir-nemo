import 'package:flutter/foundation.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';

/// Background foreground service that keeps "Hey Nemo" wake word
/// detection alive even when the app is minimized or screen is off.
///
/// The wake word detection itself still runs in WakeWordService
/// on the main Flutter isolate — this service just prevents Android
/// from killing the app.
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
        channelDescription: 'Listening for "Hey Nemo"',
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
  static Future<void> start() async {
    await init();
    if (await FlutterForegroundTask.isRunningService) return;

    await FlutterForegroundTask.startService(
      serviceId: 256,
      notificationTitle: 'Nemo',
      notificationText: 'Listening for "Hey Nemo"…',
      notificationIcon: null,
      callback: _noopCallback,
    );

    debugPrint('BackgroundWakeWordService: started');
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
    // Update notification text periodically
    FlutterForegroundTask.updateService(
      notificationTitle: 'Nemo',
      notificationText: 'Listening for "Hey Nemo"…',
    );
  }

  @override
  Future<void> onDestroy(DateTime timestamp) async {}
}
