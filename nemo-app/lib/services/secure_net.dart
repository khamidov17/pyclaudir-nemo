import 'dart:io';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

/// TLS for the self-signed server cert, with trust-on-first-use pinning.
///
/// The server (bare IP, no domain → no Let's Encrypt) presents a self-signed
/// cert. The FIRST wss/https connection stores that cert's SHA-256; every
/// later connection must present the exact same cert or it is refused — a
/// MITM with a different cert can't get in even though the cert is
/// self-signed. Pair on a trusted network (home wifi) so the first pin is
/// the real server.
class SecureNet {
  static String? _pin;
  static bool _loaded = false;

  static Future<HttpClient> httpClient() async {
    if (!_loaded) {
      _pin = await _storage.read(key: 'server_cert_sha256');
      _loaded = true;
    }
    final client = HttpClient();
    client.badCertificateCallback = (cert, host, port) {
      final fp = sha256.convert(cert.der).toString();
      if (_pin == null || _pin!.isEmpty) {
        _pin = fp;
        _storage.write(key: 'server_cert_sha256', value: fp);
        debugPrint('SecureNet: pinned server cert $fp (trust on first use)');
        return true;
      }
      final ok = fp == _pin;
      if (!ok) debugPrint('SecureNet: REJECTED cert $fp (pin mismatch — MITM?)');
      return ok;
    };
    return client;
  }

  /// Forget the pin — call when the user points the app at a new server.
  static Future<void> resetPin() async {
    _pin = null;
    _loaded = false;
    await _storage.delete(key: 'server_cert_sha256');
  }
}
