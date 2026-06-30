import 'package:flutter/foundation.dart';
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
  aOptions: AndroidOptions(
    encryptedSharedPreferences: true,
    keyCipherAlgorithm: KeyCipherAlgorithm.RSA_ECB_OAEPwithSHA_256andMGF1Padding,
  ),
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
  wake.onWakeWord = () {
    if (!voiceSession.isActive) voiceSession.start();
  };

  // Proactive TTS (reminders, briefings): skip when a voice session is live
  // to avoid fighting the VoiceChatService for the audio focus / speaker.
  nemo.audioB64.listen(
    (b64) async {
      if (voiceSession.isActive) return;
      await voice.playAudio(b64);
    },
    onError: (Object e) => debugPrint('audio stream error: $e'),
  );

  final updater = UpdateService();

  // Wire voice-active flag into UpdateService so OTA install is blocked during
  // a live voice session (prevents mic/audio disruption from installer reboot).
  voiceSession.addListener(() {
    updater.setVoiceActive(voiceSession.isActive);
  });

  // Keep Nemo alive in the background whenever paired — so scheduled reminders
  // and briefings can be spoken on time even with the phone pocketed (and so
  // wake word, when enabled, survives the screen turning off).
  if (isPaired) {
    final wakeOn = await WakeWordService.isEnabled();
    try {
      await BackgroundWakeWordService.start(
        statusText: wakeOn
            ? 'Listening for "Hey Nemo"…'
            : 'Active — ready for voice and reminders',
      );
    } catch (e) {
      debugPrint('BackgroundWakeWordService.start failed: $e');
    }
  }

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

class NemoApp extends StatefulWidget {
  final bool isPaired;
  const NemoApp({super.key, required this.isPaired});
  @override
  State<NemoApp> createState() => _NemoAppState();
}

class _NemoAppState extends State<NemoApp> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final nemo = context.read<NemoService>();
    if (state == AppLifecycleState.resumed && widget.isPaired) {
      if (!nemo.isConnected) nemo.connect();
    } else if (state == AppLifecycleState.paused) {
      // Keep socket alive for background reminders; do not disconnect.
      // The foreground service keeps the process alive.
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Nemo',
      debugShowCheckedModeBanner: false,
      navigatorKey: navigatorKey,
      theme: buildNemoTheme(),
      home: widget.isPaired ? const ChatListScreen() : const PairingScreen(),
    );
  }
}
