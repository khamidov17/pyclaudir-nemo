import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:provider/provider.dart';

import 'screens/chat_list_screen.dart';
import 'screens/pairing_screen.dart';
import 'theme.dart';
import 'services/background_service.dart';
import 'services/nemo_service.dart';
import 'services/phone_action_service.dart';
import 'services/phone_command_executor.dart';
import 'services/update_service.dart';
import 'services/voice_service.dart';
import 'services/voice_session_controller.dart';
import 'services/wake_word_service.dart';

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

/// Global navigator: lets background services (voice actions, biometric
/// prompts) reach a UI context when the app happens to be visible.
final navigatorKey = GlobalKey<NavigatorState>();

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Request mic + notification permissions upfront
  await FlutterForegroundTask.requestIgnoreBatteryOptimization();

  final serverUrl = await _storage.read(key: 'server_url') ?? '';
  final appToken = await _storage.read(key: 'app_token') ?? '';
  final isPaired = serverUrl.isNotEmpty && appToken.isNotEmpty;

  final nemo = NemoService();
  // App-global TTS playback: proactive audio (reminders, briefings) must speak
  // no matter which screen is open — or none — so the player is wired here,
  // once, instead of inside the chat screens.
  final voice = VoiceService();
  nemo.audioB64.listen((b64) => voice.playAudio(b64));
  if (isPaired) {
    nemo.configure(serverUrl, appToken);
    // Connect at launch so a scheduled reminder can reach (and speak on) the
    // phone even if the user never opens a chat screen. Auto-reconnects on drop.
    nemo.connect();
  }

  final wake = WakeWordService();
  await wake.init();

  final executor = PhoneCommandExecutor();
  final phoneActions = PhoneActionService(
    nemo,
    executor,
    () => navigatorKey.currentContext,
  );
  phoneActions.start();

  // The voice session lives at app level: "hey nemo" starts a hands-free
  // conversation from ANY app, with no UI navigation — Nemo simply talks.
  final voiceSession = VoiceSessionController(
    wake: wake,
    serverUrl: () => nemo.serverUrl,
    executor: executor,
    navigatorKey: navigatorKey,
  );
  wake.onWakeWord = () => voiceSession.start();

  // Keep Nemo alive in the background whenever paired — so scheduled reminders
  // and briefings can be spoken on time even with the phone pocketed (and so
  // wake word, when enabled, survives the screen turning off).
  if (isPaired) {
    final wakeOn = await WakeWordService.isEnabled();
    await BackgroundWakeWordService.start(
      statusText: wakeOn
          ? 'Listening for "Hey Nemo"…'
          : 'Active — ready for voice and reminders',
    );
  }

  final updater = UpdateService();
  if (isPaired) {
    Future.delayed(const Duration(seconds: 5), () {
      updater.checkForUpdate(serverUrl);
    });
  }

  runApp(
    WithForegroundTask(
      child: MultiProvider(
        providers: [
          ChangeNotifierProvider.value(value: nemo),
          ChangeNotifierProvider.value(value: voice),
          ChangeNotifierProvider.value(value: wake),
          ChangeNotifierProvider.value(value: updater),
          ChangeNotifierProvider.value(value: voiceSession),
        ],
        child: NemoApp(isPaired: isPaired),
      ),
    ),
  );
}

class NemoApp extends StatelessWidget {
  final bool isPaired;
  const NemoApp({super.key, required this.isPaired});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Nemo',
      debugShowCheckedModeBanner: false,
      navigatorKey: navigatorKey,
      theme: buildNemoTheme(),
      home: isPaired ? const ChatListScreen() : const PairingScreen(),
    );
  }
}
