import 'package:flutter/material.dart';
import 'package:local_auth/local_auth.dart';
import 'package:local_auth/error_codes.dart' as auth_error;
import 'package:local_auth_android/local_auth_android.dart';
import 'package:flutter/services.dart';

/// Biometric cascade: face → fingerprint → PIN/password.
class BiometricService {
  static final _auth = LocalAuthentication();
  static int _failCount = 0;

  static const _sensitiveVerbs = {
    'camera', 'type', 'open', 'install',
  };

  static bool isSensitive(String command) =>
      _sensitiveVerbs.contains(command.split(' ').first.toLowerCase());

  static Future<bool> authenticate(BuildContext context, String reason) async {
    if (_failCount >= 3) return _passwordFallback(context, reason);
    try {
      final canCheck = await _auth.canCheckBiometrics || await _auth.isDeviceSupported();
      if (!canCheck) return _passwordFallback(context, reason);

      final bios = await _auth.getAvailableBiometrics();

      // 1. Try face
      if (bios.contains(BiometricType.face)) {
        if (await _bio(reason, 'Use Face ID to confirm')) {
          _failCount = 0; return true;
        }
        _failCount++;
      }

      // 2. Try fingerprint
      if (bios.contains(BiometricType.fingerprint) ||
          bios.contains(BiometricType.strong)) {
        if (await _bio(reason, 'Use fingerprint to confirm')) {
          _failCount = 0; return true;
        }
        _failCount++;
      }

      // 3. Device credentials (PIN/pattern/password)
      return _deviceCreds(context, reason);
    } on PlatformException catch (e) {
      if (e.code == auth_error.notAvailable || e.code == auth_error.notEnrolled) {
        return _deviceCreds(context, reason);
      }
      return false;
    }
  }

  static Future<bool> _bio(String reason, String hint) async {
    try {
      return await _auth.authenticate(
        localizedReason: reason,
        authMessages: [
          AndroidAuthMessages(
            signInTitle: 'Nemo confirmation',
            biometricHint: hint,
            cancelButton: 'Deny',
          ),
        ],
        options: const AuthenticationOptions(biometricOnly: true, stickyAuth: true),
      );
    } catch (_) { return false; }
  }

  static Future<bool> _deviceCreds(BuildContext context, String reason) async {
    try {
      return await _auth.authenticate(
        localizedReason: reason,
        authMessages: const [
          AndroidAuthMessages(signInTitle: 'Nemo confirmation', cancelButton: 'Deny'),
        ],
        options: const AuthenticationOptions(biometricOnly: false, stickyAuth: true),
      );
    } catch (_) { return _passwordFallback(context, reason); }
  }

  static Future<bool> _passwordFallback(BuildContext context, String reason) async {
    if (!context.mounted) return false;
    final ctrl = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (_) => AlertDialog(
        backgroundColor: const Color(0xFF1E1E2E),
        title: const Text('Confirm action', style: TextStyle(color: Colors.white)),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(reason, style: const TextStyle(color: Colors.white70, fontSize: 13)),
          const SizedBox(height: 12),
          TextField(
            controller: ctrl, obscureText: true, autofocus: true,
            style: const TextStyle(color: Colors.white),
            decoration: const InputDecoration(labelText: 'PIN / password',
                labelStyle: TextStyle(color: Colors.white54)),
          ),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Deny')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Confirm')),
        ],
      ),
    );
    if (ok == true) { _failCount = 0; return true; }
    return false;
  }
}
