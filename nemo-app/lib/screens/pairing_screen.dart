import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../app_version.dart';
import 'home_screen.dart';

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
  final _urlCtrl = TextEditingController(text: 'ws://165.140.240.169:8765');
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
    setState(() { _saving = true; _error = null; });
    await _storage.write(key: 'server_url', value: url);
    await _storage.write(key: 'app_token', value: token);
    if (mounted) {
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (_) => const HomeScreen()),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F0F1A),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('👋', style: TextStyle(fontSize: 48)),
              const SizedBox(height: 16),
              const Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text('Pair with Nemo',
                      style: TextStyle(color: Colors.white, fontSize: 28,
                          fontWeight: FontWeight.bold)),
                  SizedBox(width: 8),
                  Padding(
                    padding: EdgeInsets.only(bottom: 3),
                    child: Text(nemoVersionLabel,
                        style: TextStyle(color: Colors.white38, fontSize: 13,
                            fontWeight: FontWeight.w600)),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              const Text('Enter your server address and the token from .env',
                  style: TextStyle(color: Colors.white54, fontSize: 14)),
              const SizedBox(height: 32),
              _field(_urlCtrl, 'Server URL', 'ws://YOUR_SERVER_IP:8765'),
              const SizedBox(height: 16),
              _field(_tokenCtrl, 'App Token', 'NEMO_APP_TOKEN from .env',
                  obscure: true),
              if (_error != null) ...[
                const SizedBox(height: 8),
                Text(_error!, style: const TextStyle(color: Colors.redAccent)),
              ],
              const SizedBox(height: 32),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: _saving ? null : _save,
                  child: _saving
                      ? const SizedBox(width: 20, height: 20,
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
      style: const TextStyle(color: Colors.white),
      decoration: InputDecoration(
        labelText: label, hintText: hint,
        hintStyle: const TextStyle(color: Colors.white24),
        filled: true, fillColor: const Color(0xFF1E1E2E),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
      ),
    );
  }

  @override
  void dispose() {
    _urlCtrl.dispose();
    _tokenCtrl.dispose();
    super.dispose();
  }
}
