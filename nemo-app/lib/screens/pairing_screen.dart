import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../app_version.dart';
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
    if (!url.startsWith('ws://') && !url.startsWith('wss://')) {
      setState(() => _error = 'URL must start with ws:// or wss://');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
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
