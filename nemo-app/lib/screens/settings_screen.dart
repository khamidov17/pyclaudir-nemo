import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:provider/provider.dart';
import '../services/nemo_service.dart';
import '../services/wake_word_service.dart';

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
    setState(() {});
  }

  Future<void> _toggleWake(bool on) async {
    setState(() => _wakeEnabled = on);
    await WakeWordService.setEnabled(on);
    if (!mounted) return;
    final wake = context.read<WakeWordService>();
    if (on) {
      await wake.start();
    } else {
      await wake.stop();
    }
  }

  Future<void> _save() async {
    await _storage.write(key: 'server_url', value: _urlController.text.trim());
    await _storage.write(key: 'app_token', value: _tokenController.text.trim());

    // Reconfigure the live NemoService
    if (mounted) {
      context.read<NemoService>().configure(
        _urlController.text.trim(),
        _tokenController.text.trim(),
      );
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Saved — reconnecting…')),
      );
      Navigator.pop(context, true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F0F1A),
      appBar: AppBar(
        title: const Text('Settings'),
        backgroundColor: const Color(0xFF0F0F1A),
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          _field(
            controller: _urlController,
            label: 'Server URL',
            hint: 'ws://165.140.240.169:8765',
            helper: 'Local: ws://IP:8765  •  VPS: ws://IP:8765',
          ),
          const SizedBox(height: 16),
          _field(
            controller: _tokenController,
            label: 'App Token',
            hint: 'NEMO_APP_TOKEN from .env',
            obscure: true,
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: const Color(0xFF1E1E2E),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _wakeEnabled,
                  onChanged: _toggleWake,
                  title: const Text('🎤 Wake word',
                      style: TextStyle(
                          color: Colors.white70, fontWeight: FontWeight.bold)),
                  subtitle: const Text(
                    'Say "nemo" / "hey nemo" to open Gemini voice. On-device, no '
                    'API key. Off by default — when on, the phone keeps the mic '
                    'open and may chime as it re-listens.',
                    style: TextStyle(color: Colors.white38, fontSize: 13),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 32),
          FilledButton(
            onPressed: _save,
            child: const Text('Save & Reconnect'),
          ),
        ],
      ),
    );
  }

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
      style: const TextStyle(color: Colors.white),
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        helperText: helper,
        helperMaxLines: 2,
        filled: true,
        fillColor: const Color(0xFF1E1E2E),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
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
