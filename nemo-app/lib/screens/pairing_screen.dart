import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/io_client.dart';
import '../app_version.dart';
import '../services/secure_net.dart';
import '../theme.dart';
import '../widgets/voice_orb.dart';
import 'chat_list_screen.dart';

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

/// First-launch pairing screen. Credentials stored encrypted, never in plain
/// SharedPreferences or shipped as app defaults.
class PairingScreen extends StatefulWidget {
  const PairingScreen({super.key});

  @override
  State<PairingScreen> createState() => _PairingScreenState();
}

class _PairingScreenState extends State<PairingScreen> {
  final _urlCtrl = TextEditingController(text: 'wss://165.140.240.169:8765');
  final _tokenCtrl = TextEditingController();
  bool _saving = false;
  String? _error;

  Future<void> _save() async {
    final url = _urlCtrl.text.trim();
    final token = _tokenCtrl.text.trim();
    if (url.isEmpty || token.isEmpty) {
      setState(() => _error = 'Both fields required');
      return;
    }
    if (url.startsWith('ws://')) {
      setState(() => _error = 'Use wss:// (encrypted). Plain ws:// is not allowed.');
      return;
    }
    if (!url.startsWith('wss://')) {
      setState(() => _error = 'URL must start with wss://');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    // Verify connection before saving credentials (BUG-02/pairing).
    // Reset TOFU pin so we probe the new server with a fresh pin slot.
    await SecureNet.resetPin();
    final httpUrl = url.replaceFirst('ws://', 'https://').replaceFirst('wss://', 'https://');
    try {
      final client = IOClient(await SecureNet.httpClient());
      final resp = await client
          .get(
            Uri.parse('$httpUrl/health'),
            headers: {'Authorization': 'Bearer $token'},
          )
          .timeout(const Duration(seconds: 8));
      client.close();
      if (resp.statusCode != 200) {
        if (mounted) setState(() { _saving = false; _error = 'Server rejected credentials (${resp.statusCode})'; });
        return;
      }
    } catch (e) {
      if (mounted) setState(() { _saving = false; _error = 'Cannot reach server: $e'; });
      return;
    }
    await _storage.write(key: 'server_url', value: url);
    await _storage.write(key: 'app_token', value: token);
    if (mounted) {
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (_) => const ChatListScreen()),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(28, 48, 28, 28),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              const VoiceOrb(state: OrbState.idle, size: 150),
              const SizedBox(height: 36),
              const Text(
                'Nemo',
                style: TextStyle(
                  color: NemoColors.text,
                  fontSize: 34,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 4,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                'Your private voice assistant  ·  $nemoVersionLabel',
                style:
                    const TextStyle(color: NemoColors.textFaint, fontSize: 13),
              ),
              const SizedBox(height: 44),
              _field(_urlCtrl, 'Server URL', 'ws://YOUR_SERVER_IP:8765'),
              const SizedBox(height: 16),
              _field(_tokenCtrl, 'App Token', 'NEMO_APP_TOKEN from .env',
                  obscure: true),
              if (_error != null) ...[
                const SizedBox(height: 14),
                Text(_error!, style: const TextStyle(color: NemoColors.danger)),
              ],
              const SizedBox(height: 32),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: _saving ? null : _save,
                  child: _saving
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Text('Connect'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _field(TextEditingController ctrl, String label, String hint,
      {bool obscure = false}) {
    return TextField(
      controller: ctrl,
      obscureText: obscure,
      style: const TextStyle(color: NemoColors.text),
      decoration: InputDecoration(labelText: label, hintText: hint),
    );
  }

  @override
  void dispose() {
    _urlCtrl.dispose();
    _tokenCtrl.dispose();
    super.dispose();
  }
}
