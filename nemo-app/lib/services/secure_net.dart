import 'dart:io';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

/// TLS certificate pinning for the self-signed Nemo server.
///
/// The server (bare IP, no domain → no Let's Encrypt) presents a self-signed
/// cert. We PIN its SHA-256 at build time, so even the very first connection
/// only trusts the real server's cert — a MITM with any other cert is refused,
/// closing the trust-on-first-use window. If `_pinnedCertSha256` is ever
/// blanked, we fall back to trust-on-first-use (store the first cert seen).
class SecureNet {
  /// SHA-256 of the server's TLS cert DER (scripts/gen_server_cert.sh). Update
  /// this if the server cert is regenerated, then rebuild the app.
  static const _pinnedCertSha256 =
      '43bc0307d60846aac237243fa00cefe61b058e69c8b7effecb74b727819fde27';

  static String? _pin;
  static bool _loaded = false;
  // Set when the user points the app at a DIFFERENT server (resetPin). The
  // build-time pin is for the known server; a deliberate server change falls
  // back to trust-on-first-use for the new host.
  static bool _tofuOverride = false;

  static Future<HttpClient> httpClient() async {
    if (!_loaded) {
      _pin = (_pinnedCertSha256.isNotEmpty && !_tofuOverride)
          ? _pinnedCertSha256
          : await _storage.read(key: 'server_cert_sha256');
      _loaded = true;
    }
    final client = HttpClient();
    client.badCertificateCallback = (cert, host, port) {
      final fp = sha256.convert(cert.der).toString().toLowerCase();
      if (_pin == null || _pin!.isEmpty) {
        // No build-time pin set → trust-on-first-use fallback.
        _pin = fp;
        _storage.write(key: 'server_cert_sha256', value: fp);
        debugPrint('SecureNet: pinned server cert $fp (TOFU)');
        return true;
      }
      final ok = fp == _pin;
      if (!ok) debugPrint('SecureNet: REJECTED cert $fp (pin mismatch — MITM?)');
      return ok;
    };
    return client;
  }

  /// Re-pin for a NEW server (user changed the Server URL). Drops the stored
  /// pin and switches to trust-on-first-use so the next connection pins the new
  /// host's cert — otherwise the build-time pin would reject any other server.
  static Future<void> resetPin() async {
    _tofuOverride = true;
    _pin = null;
    _loaded = false;
    await _storage.delete(key: 'server_cert_sha256');
  }
}
