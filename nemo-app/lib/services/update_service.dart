import 'dart:convert';
import 'dart:io';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'secure_net.dart';

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

/// OTA update — authenticated download with SHA-256 verification BEFORE the
/// installer is ever invoked. The token travels in the Authorization header,
/// never in a URL (browser history / proxy logs). Browser hand-off survives
/// only as a fallback for ROMs that block app-initiated installs.
class UpdateService extends ChangeNotifier {
  bool _updateAvailable = false;
  int _serverVersion = 0;
  String _serverSha256 = '';
  bool _downloading = false;
  double _downloadProgress = 0.0;

  bool get updateAvailable => _updateAvailable;
  bool get isDownloading => _downloading;
  double get downloadProgress => _downloadProgress;
  int get serverVersion => _serverVersion;

  static const _intents = MethodChannel('com.avazbek.nemo_app/intents');

  Future<void> checkForUpdate(String serverBaseUrl) async {
    try {
      final token = await _storage.read(key: 'app_token') ?? '';
      final httpUrl = _toHttp(serverBaseUrl);
      final client = IOClient(await SecureNet.httpClient());
      final http.Response res;
      try {
        res = await client
            .get(
              Uri.parse('$httpUrl/apk/version'),
              headers: {'Authorization': 'Bearer $token'},
            )
            .timeout(const Duration(seconds: 8));
      } finally {
        client.close();
      }
      if (res.statusCode != 200) return;

      final body = jsonDecode(res.body) as Map<String, dynamic>;
      _serverVersion = (body['version'] as num?)?.toInt() ?? 0;
      _serverSha256 = (body['sha256'] as String? ?? '').toLowerCase();

      final installed = await _getInstalledVersion();
      if (_serverVersion > installed) {
        _updateAvailable = true;
        notifyListeners();
      }
    } catch (e) {
      debugPrint('Update check failed: $e');
    }
  }

  /// Whether it is safe to install right now (no active voice session).
  bool get canInstall => !_voiceActive;
  bool _voiceActive = false;
  void setVoiceActive(bool active) {
    _voiceActive = active;
  }

  /// Download → verify SHA-256 → hand the VERIFIED file to the installer.
  /// Throws [UpdateException] on any failure so the UI can offer the
  /// browser fallback explicitly.
  Future<void> downloadAndInstall(String serverBaseUrl) async {
    if (_downloading) return;
    if (_voiceActive) {
      throw UpdateException('cannot install during an active voice session');
    }
    if (_serverSha256.isEmpty) {
      await checkForUpdate(serverBaseUrl);
      if (_serverSha256.isEmpty) {
        throw UpdateException('server did not provide an APK hash');
      }
    }
    _downloading = true;
    _downloadProgress = 0.0;
    notifyListeners();
    try {
      final file = await _download(serverBaseUrl);
      // Re-check: a voice session may have started during the download (up to 30s).
      if (_voiceActive) {
        file.delete().catchError((_) {});
        throw UpdateException('voice session started during download — retry after call');
      }
      bool ok = false;
      try {
        ok = await _intents.invokeMethod<bool>('installApk', {'path': file.path}) ?? false;
      } finally {
        file.delete().catchError((_) {});
      }
      if (!ok) throw UpdateException('installer could not be launched');
      _updateAvailable = false;
    } finally {
      _downloading = false;
      notifyListeners();
    }
  }

  Future<File> _download(String serverBaseUrl) async {
    final token = await _storage.read(key: 'app_token') ?? '';
    final url = '${_toHttp(serverBaseUrl)}/apk/download';
    final req = http.Request('GET', Uri.parse(url))
      ..headers['Authorization'] = 'Bearer $token';
    final client = IOClient(await SecureNet.httpClient());
    final http.StreamedResponse res;
    try {
      res = await client.send(req).timeout(const Duration(seconds: 30));
    } catch (e) {
      client.close();
      throw UpdateException('download failed: $e');
    }
    if (res.statusCode != 200) {
      client.close();
      throw UpdateException('download failed (HTTP ${res.statusCode})');
    }

    final dir = Directory('${(await getTemporaryDirectory()).path}/apk');
    await dir.create(recursive: true);
    final file = File('${dir.path}/Nemo.apk');
    final sink = file.openWrite();
    final total = res.contentLength ?? 0;
    var received = 0;
    final digestSink = _DigestSink();
    final hasher = sha256.startChunkedConversion(digestSink);
    try {
      await for (final chunk in res.stream) {
        sink.add(chunk);
        hasher.add(chunk);
        received += chunk.length;
        if (total > 0) _setProgress(received / total);
      }
    } catch (e) {
      await sink.close().catchError((_) {});
      client.close();
      await file.delete().catchError((_) {});
      throw UpdateException('download failed: $e');
    }
    await sink.close();
    client.close();
    hasher.close();

    final actual = digestSink.digest.toString().toLowerCase();
    if (!_constantTimeEqual(actual, _serverSha256)) {
      await file.delete();
      _updateAvailable = false;
      _serverSha256 = ''; // force re-fetch on next attempt — stale hash would loop
      throw UpdateException(
          'APK hash mismatch — refused to install (possible tampering)');
    }
    return file;
  }

  void _setProgress(double p) {
    // Throttle UI churn to whole-percent steps.
    if ((p * 100).floor() == (_downloadProgress * 100).floor()) return;
    _downloadProgress = p;
    notifyListeners();
  }

  /// Last-resort fallback for ROMs that silently block app-initiated installs
  /// (MIUI). No hash check is possible on this path — keep it user-triggered.
  /// Uses a ONE-TIME download token (fetched with the header-authed app token)
  /// so the long-lived app token never lands in browser history.
  Future<void> openDownloadInBrowser(String serverBaseUrl) async {
    final token = await _storage.read(key: 'app_token') ?? '';
    final base = _toHttp(serverBaseUrl);
    // Get a one-time download token. NEVER fall back to the long-lived app
    // token in a browser URL (it would leak the master credential into browser
    // history); abort instead — the in-app verified download is the primary path.
    String? dlToken;
    try {
      final client = IOClient(await SecureNet.httpClient());
      try {
        final res = await client.get(
          Uri.parse('$base/apk/dltoken'),
          headers: {'Authorization': 'Bearer $token'},
        ).timeout(const Duration(seconds: 8));
        if (res.statusCode == 200) {
          dlToken = jsonDecode(res.body)['token'] as String?;
        }
      } finally {
        client.close();
      }
    } catch (e) {
      debugPrint('dltoken fetch failed: $e');
    }
    if (dlToken == null || dlToken.isEmpty) {
      throw UpdateException(
          'Could not get a download link — use the in-app update instead.');
    }
    await _intents.invokeMethod('openUrl', {'url': '$base/apk/download?token=$dlToken'});
    // Keep _updateAvailable=true — browser fallback doesn't confirm install success.
    // The banner clears when the new build starts (checkForUpdate comparison).
    notifyListeners();
  }

  /// The version actually running on the device — the real source of truth.
  Future<int> _getInstalledVersion() async {
    try {
      final info = await PackageInfo.fromPlatform();
      final v = int.tryParse(info.buildNumber.trim());
      // Non-numeric buildNumber (empty / "SNAPSHOT" / debug) → treat as
      // "very new" so a dev build never triggers a spurious update banner.
      return v ?? 999999;
    } catch (_) {
      return 0;
    }
  }

  static String _toHttp(String wsUrl) => wsUrl
      .replaceFirst('wss://', 'https://')
      .replaceFirst('ws://', 'http://');
}

/// Constant-time string comparison to prevent timing-oracle attacks on
/// SHA-256 hash verification (BUG-012: early-exit == leaks match length).
bool _constantTimeEqual(String a, String b) {
  if (a.length != b.length) return false;
  int diff = 0;
  for (int i = 0; i < a.length; i++) {
    diff |= a.codeUnitAt(i) ^ b.codeUnitAt(i);
  }
  return diff == 0;
}

class UpdateException implements Exception {
  final String message;
  UpdateException(this.message);
  @override
  String toString() => message;
}

class _DigestSink implements Sink<Digest> {
  late Digest digest;
  @override
  void add(Digest data) => digest = data;
  @override
  void close() {}
}
