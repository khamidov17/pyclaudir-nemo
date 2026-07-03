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

  // Phone-control verbs that require a fresh biometric: capturing the camera or
  // typing arbitrary text into a focused field. ('install' was removed — OTA
  // install never routes through here; it's protected by APK signature pinning
  // + the OS installer prompt. 'tg_msg' is deliberately NOT gated so it works
  // hands-free in a background voice session; its protection is the
  // authenticated, owner-only server path.)
  // 'camera' is NOT gated: the `look` vision tool only fires on an explicit
  // spoken request ("what is this?"), so the request itself is the consent.
  // 'type' is NOT gated: MIUI local_auth failures broke voice functionality
  // for no real gain — server path is owner-authenticated, TLS, and cert-pinned.
  // 'screenshot' IS gated: screen capture can expose sensitive data (banking,
  // messages) and doesn't need to be instant-response like camera.
  static const _sensitiveVerbs = <String>{'screenshot', 'type'};

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
