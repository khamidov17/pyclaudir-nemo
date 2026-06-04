import 'dart:convert';
import 'dart:io';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:open_file/open_file.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

/// OTA update — authenticated, with APK hash verification before install.
class UpdateService extends ChangeNotifier {
  static const _versionKey = 'installed_apk_version';

  bool _updateAvailable = false;
  int _serverVersion = 0;
  String _serverHash = '';
  bool _downloading = false;
  double _downloadProgress = 0.0;

  bool get updateAvailable => _updateAvailable;
  bool get isDownloading => _downloading;
  double get downloadProgress => _downloadProgress;
  int get serverVersion => _serverVersion;

  Future<void> checkForUpdate(String serverBaseUrl) async {
    try {
      final token = await _storage.read(key: 'app_token') ?? '';
      final httpUrl = _toHttp(serverBaseUrl);
      final res = await http
          .get(
            Uri.parse('$httpUrl/apk/version'),
            headers: {'Authorization': 'Bearer $token'},
          )
          .timeout(const Duration(seconds: 8));
      if (res.statusCode != 200) return;

      final body = jsonDecode(res.body) as Map<String, dynamic>;
      _serverVersion = (body['version'] as num?)?.toInt() ?? 0;
      _serverHash = body['sha256'] as String? ?? '';

      final installed = await _getInstalledVersion();
      if (_serverVersion > installed) {
        _updateAvailable = true;
        notifyListeners();
      }
    } catch (e) {
      debugPrint('Update check failed: $e');
    }
  }

  Future<void> downloadAndInstall(String serverBaseUrl) async {
    if (_downloading) return;
    _downloading = true;
    _downloadProgress = 0.0;
    notifyListeners();

    try {
      final token = await _storage.read(key: 'app_token') ?? '';
      final httpUrl = _toHttp(serverBaseUrl);
      final uri = Uri.parse('$httpUrl/apk/download');

      final req = http.Request('GET', uri)
        ..headers['Authorization'] = 'Bearer $token';
      final response = await req.send().timeout(const Duration(minutes: 5));
      if (response.statusCode != 200) return;

      final total = response.contentLength ?? 0;
      final dir = await getTemporaryDirectory();
      final file = File('${dir.path}/nemo-update.apk');
      final sink = file.openWrite();
      int received = 0;

      await for (final chunk in response.stream) {
        sink.add(chunk);
        received += chunk.length;
        if (total > 0) {
          _downloadProgress = received / total;
          notifyListeners();
        }
      }
      await sink.close();

      // Verify APK hash before opening installer
      if (_serverHash.isNotEmpty) {
        final bytes = await file.readAsBytes();
        final actual = sha256.convert(bytes).toString();
        if (actual != _serverHash) {
          debugPrint('APK hash mismatch! expected=$_serverHash actual=$actual');
          await file.delete();
          return;
        }
        debugPrint('APK hash verified ✓');
      }

      await OpenFile.open(file.path);
      await _markInstalled(_serverVersion);
      _updateAvailable = false;
    } catch (e) {
      debugPrint('Download/install error: $e');
    } finally {
      _downloading = false;
      notifyListeners();
    }
  }

  Future<int> _getInstalledVersion() async {
    final stored = int.tryParse(await _storage.read(key: _versionKey) ?? '') ?? 0;
    try {
      final info = await PackageInfo.fromPlatform();
      final buildNumber = int.tryParse(info.buildNumber) ?? 0;
      return buildNumber > stored ? buildNumber : stored;
    } catch (_) {
      return stored > 0 ? stored : 1;
    }
  }

  Future<void> _markInstalled(int version) async {
    await _storage.write(key: _versionKey, value: version.toString());
  }

  static String _toHttp(String wsUrl) => wsUrl
      .replaceFirst('wss://', 'https://')
      .replaceFirst('ws://', 'http://');
}
