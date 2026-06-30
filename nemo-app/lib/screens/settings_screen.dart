import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:provider/provider.dart';
import '../services/background_service.dart';
import '../services/messages_service.dart';
import '../services/nemo_service.dart';
import '../services/secure_net.dart';
import '../services/update_service.dart';
import '../services/voice_options.dart';
import '../services/wake_word_service.dart';
import '../theme.dart';

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _urlController = TextEditingController();
  final _tokenController = TextEditingController();
  bool _wakeEnabled = false;
  String _voiceId = kDefaultVoiceId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    // Read from encrypted storage — same as pairing screen writes
    _urlController.text = await _storage.read(key: 'server_url') ?? '';
    _tokenController.text = await _storage.read(key: 'app_token') ?? '';
    _wakeEnabled = await WakeWordService.isEnabled();
    _voiceId = await _storage.read(key: 'nemo_voice') ?? kDefaultVoiceId;
    setState(() {});
  }

  Future<void> _setVoice(String id) async {
    setState(() => _voiceId = id);
    await _storage.write(key: 'nemo_voice', value: id);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Voice updated — starts on your next "hey nemo".')),
    );
  }

  Future<void> _toggleWake(bool on) async {
    setState(() => _wakeEnabled = on);
    await WakeWordService.setEnabled(on);
    if (!mounted) return;
    final wake = context.read<WakeWordService>();
    if (on) {
      // The foreground service is what keeps wake-word listening alive when the
      // app is backgrounded / screen off — without it Android suspends the app.
      await BackgroundWakeWordService.start(
        statusText: 'Listening for "Hey Nemo"…',
      );
      await wake.start();
    } else {
      await wake.stop();
      await BackgroundWakeWordService.stop();
    }
  }

  Future<void> _checkUpdate() async {
    final messenger = ScaffoldMessenger.of(context);
    final updater = context.read<UpdateService>();
    void snack(String m) => messenger.showSnackBar(SnackBar(content: Text(m)));
    final url = (await _storage.read(key: 'server_url') ?? '').trim();
    if (url.isEmpty) {
      snack('Add your server URL and token first.');
      return;
    }
    snack('Checking for updates…');
    await updater.checkForUpdate(url);
    if (!mounted) return;
    if (updater.updateAvailable) {
      snack('Nemo v${updater.serverVersion} found — downloading…');
      try {
        await updater.downloadAndInstall(url);
      } catch (e) {
        snack('Verified install failed — trying browser.');
        try {
          await updater.openDownloadInBrowser(url);
        } catch (e2) {
          snack('$e2');
        }
      }
    } else {
      snack("You're on the latest version.");
    }
  }

  Future<void> _save() async {
    final previousUrl = await _storage.read(key: 'server_url') ?? '';
    await _storage.write(key: 'server_url', value: _urlController.text.trim());
    await _storage.write(key: 'app_token', value: _tokenController.text.trim());
    // New server → forget the old TLS pin so the next connect re-pins.
    if (previousUrl != _urlController.text.trim()) {
      await SecureNet.resetPin();
    }

    // Reconfigure the live NemoService and force a reconnect with new creds.
    if (mounted) {
      final nemo = context.read<NemoService>();
      nemo.disconnect(); // close existing connection first
      nemo.configure(   // resets _manualDisconnect=false + stores new creds
        _urlController.text.trim(),
        _tokenController.text.trim(),
      );
      unawaited(nemo.connect()); // reconnect; auto-retry enabled by configure()
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Saved — reconnecting…')),
      );
      Navigator.pop(context, true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
        children: [
          _sectionLabel('Connection'),
          _card(Column(children: [
            _field(
              controller: _urlController,
              label: 'Server URL',
              hint: 'wss://165.140.240.169:8765',
              helper: 'Encrypted (wss://). The app pins the server certificate '
                  'on first connect.',
            ),
            const SizedBox(height: 16),
            _field(
              controller: _tokenController,
              label: 'App Token',
              hint: 'NEMO_APP_TOKEN from .env',
              obscure: true,
            ),
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: _save,
                child: const Text('Save & Reconnect'),
              ),
            ),
          ])),
          const SizedBox(height: 24),

          _sectionLabel('Voice'),
          _card(SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: _wakeEnabled,
            onChanged: _toggleWake,
            title: const Text('Wake word',
                style: TextStyle(
                    color: NemoColors.text, fontWeight: FontWeight.w600)),
            subtitle: const Text(
              'Say "nemo" / "hey nemo" to open Nemo voice. On-device, no '
              'API key. Off by default.',
              style: TextStyle(color: NemoColors.textDim, fontSize: 13),
            ),
          )),
          const SizedBox(height: 12),

          _sectionLabel('Messages'),
          _card(FutureBuilder<bool>(
            future: MessageAwareness.isAccessGranted(),
            builder: (_, snap) {
              final granted = snap.data ?? false;
              return ListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Message awareness',
                    style: TextStyle(
                        color: NemoColors.text, fontWeight: FontWeight.w600)),
                subtitle: Text(
                  granted
                      ? 'On. Ask "check my messages" — Nemo reads recent '
                          'Telegram/WhatsApp. Codes are hidden; nothing is stored.'
                      : 'Off. Grant notification access so Nemo can read your '
                          'latest Telegram/WhatsApp on request.',
                  style:
                      const TextStyle(color: NemoColors.textDim, fontSize: 13),
                ),
                trailing: FilledButton(
                  onPressed: () async {
                    await MessageAwareness.openAccessSettings();
                    if (mounted) setState(() {});
                  },
                  child: Text(granted ? 'Manage' : 'Enable'),
                ),
              );
            },
          )),
          const SizedBox(height: 24),

          // VOICE_PICKER_SLOT
          _card(Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Row(children: [
                Icon(Icons.graphic_eq, color: NemoColors.accent, size: 20),
                SizedBox(width: 10),
                Text('Nemo voice',
                    style: TextStyle(
                        color: NemoColors.text, fontWeight: FontWeight.w600)),
              ]),
              const SizedBox(height: 4),
              const Text('Pick how Nemo sounds. Takes effect next time you talk.',
                  style: TextStyle(color: NemoColors.textDim, fontSize: 13)),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: _voiceId,
                isExpanded: true,
                dropdownColor: NemoColors.surface,
                items: [
                  for (final v in kNemoVoices)
                    DropdownMenuItem(
                      value: v.id,
                      child: Text('${v.label} — ${v.blurb}',
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: NemoColors.text)),
                    ),
                ],
                onChanged: (id) {
                  if (id != null) _setVoice(id);
                },
              ),
            ],
          )),
          const SizedBox(height: 24),

          _sectionLabel('About'),
          Consumer<UpdateService>(
            builder: (_, updater, __) {
              if (updater.isDownloading) {
                return OutlinedButton.icon(
                  onPressed: null,
                  icon: const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                  label: Text(
                    'Downloading ${(updater.downloadProgress * 100).toStringAsFixed(0)}%…',
                  ),
                );
              }
              return OutlinedButton.icon(
                onPressed: _checkUpdate,
                icon: const Icon(Icons.system_update),
                label: const Text('Check for updates'),
              );
            },
          ),
        ],
      ),
    );
  }

  Widget _sectionLabel(String text) => Padding(
        padding: const EdgeInsets.only(left: 4, bottom: 10, top: 4),
        child: Text(
          text.toUpperCase(),
          style: const TextStyle(
            color: NemoColors.textFaint,
            fontSize: 12,
            fontWeight: FontWeight.w600,
            letterSpacing: 1.5,
          ),
        ),
      );

  Widget _card(Widget child) => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: NemoColors.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: NemoColors.border),
        ),
        child: child,
      );

  Widget _field({
    required TextEditingController controller,
    required String label,
    String? hint,
    String? helper,
    bool obscure = false,
  }) {
    return TextField(
      controller: controller,
      obscureText: obscure,
      style: const TextStyle(color: NemoColors.text),
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        helperText: helper,
      ),
    );
  }

  @override
  void dispose() {
    _urlController.dispose();
    _tokenController.dispose();
    super.dispose();
  }
}
