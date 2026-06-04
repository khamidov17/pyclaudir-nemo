import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:provider/provider.dart';

import 'screens/chat_list_screen.dart';
import 'screens/pairing_screen.dart';
import 'services/background_service.dart';
import 'services/nemo_service.dart';
import 'services/phone_action_service.dart';
import 'services/update_service.dart';
import 'services/voice_service.dart';
import 'services/wake_word_service.dart';

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Request mic + notification permissions upfront
  await FlutterForegroundTask.requestIgnoreBatteryOptimization();

  final serverUrl = await _storage.read(key: 'server_url') ?? '';
  final appToken = await _storage.read(key: 'app_token') ?? '';
  final isPaired = serverUrl.isNotEmpty && appToken.isNotEmpty;

  final nemo = NemoService();
  if (isPaired) nemo.configure(serverUrl, appToken);

  final wake = WakeWordService();
  await wake.init();

  final phoneActions = PhoneActionService(nemo);
  phoneActions.start();

  // Start background foreground service so wake word works with screen off.
  // Off by default — only when the user has opted in via Settings.
  if (isPaired && await WakeWordService.isEnabled()) {
    await BackgroundWakeWordService.start();
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
          ChangeNotifierProvider(create: (_) => VoiceService()),
          ChangeNotifierProvider.value(value: wake),
          ChangeNotifierProvider.value(value: updater),
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
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF7C3AED),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: isPaired ? const ChatListScreen() : const PairingScreen(),
    );
  }
}
