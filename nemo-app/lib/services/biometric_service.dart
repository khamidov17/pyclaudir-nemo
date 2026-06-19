import 'package:flutter/material.dart';
import 'package:local_auth/local_auth.dart';
import 'package:local_auth/error_codes.dart' as auth_error;
import 'package:local_auth_android/local_auth_android.dart';
import 'package:flutter/services.dart';

/// Biometric cascade: face → fingerprint → device PIN/pattern/password.
/// Fails CLOSED: if no system-verified method is available, the action is
/// denied. (A previous in-app "PIN dialog" fallback accepted any input and
/// was a fake gate — never reintroduce it.)
class BiometricService {
  static final _auth = LocalAuthentication();
  static int _failCount = 0;

  // Actions that capture, type arbitrary text into a focused field, or
  // install software require a fresh biometric confirmation. (Messaging via
  // tg_msg is deliberately NOT here: it must work hands-free in a background
  // voice session, where no UI is available to confirm. Its protection is the
  // authenticated, owner-only server path — not an in-app prompt.)
  static const _sensitiveVerbs = {
    'camera', 'type', 'install',
  };

  static bool isSensitive(String command) =>
      _sensitiveVerbs.contains(command.split(' ').first.toLowerCase());

  static Future<bool> authenticate(BuildContext context, String reason) async {
    if (_failCount >= 3) return _deviceCreds(reason);
    try {
      final canCheck = await _auth.canCheckBiometrics || await _auth.isDeviceSupported();
      if (!canCheck) return _deviceCreds(reason);

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

      // 3. Device credentials (PIN/pattern/password — system-verified)
      return _deviceCreds(reason);
    } on PlatformException catch (e) {
      if (e.code == auth_error.notAvailable || e.code == auth_error.notEnrolled) {
        return _deviceCreds(reason);
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

  static Future<bool> _deviceCreds(String reason) async {
    try {
      final ok = await _auth.authenticate(
        localizedReason: reason,
        authMessages: const [
          AndroidAuthMessages(signInTitle: 'Nemo confirmation', cancelButton: 'Deny'),
        ],
        options: const AuthenticationOptions(biometricOnly: false, stickyAuth: true),
      );
      if (ok) _failCount = 0;
      return ok;
    } catch (_) {
      return false;
    }
  }
}
