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
      final res = await client
          .get(
            Uri.parse('$httpUrl/apk/version'),
            headers: {'Authorization': 'Bearer $token'},
          )
          .timeout(const Duration(seconds: 8));
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

  /// Download → verify SHA-256 → hand the VERIFIED file to the installer.
  /// Throws [UpdateException] on any failure so the UI can offer the
  /// browser fallback explicitly.
  Future<void> downloadAndInstall(String serverBaseUrl) async {
    if (_downloading) return;
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
      final ok = await _intents.invokeMethod<bool>(
            'installApk', {'path': file.path},
          ) ??
          false;
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
    final res = await client.send(req).timeout(const Duration(seconds: 30));
    if (res.statusCode != 200) {
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
    } finally {
      await sink.close();
    }
    hasher.close();

    final actual = digestSink.digest.toString().toLowerCase();
    if (actual != _serverSha256) {
      await file.delete();
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
  Future<void> openDownloadInBrowser(String serverBaseUrl) async {
    final token = await _storage.read(key: 'app_token') ?? '';
    final url = '${_toHttp(serverBaseUrl)}/apk/download?token=$token';
    await _intents.invokeMethod('openUrl', {'url': url});
    _updateAvailable = false;
    notifyListeners();
  }

  /// The version actually running on the device — the real source of truth.
  Future<int> _getInstalledVersion() async {
    try {
      final info = await PackageInfo.fromPlatform();
      return int.tryParse(info.buildNumber) ?? 0;
    } catch (_) {
      return 0;
    }
  }

  static String _toHttp(String wsUrl) => wsUrl
      .replaceFirst('wss://', 'https://')
      .replaceFirst('ws://', 'http://');
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
