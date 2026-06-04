import 'dart:async';
import 'dart:convert';
import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter/foundation.dart';
import 'biometric_service.dart';
import 'nemo_service.dart';

/// Listens for action commands from the backend and executes them on the phone.
/// Results are sent back via NemoService.sendActionResult().
class PhoneActionService {
  static const _accessibility = MethodChannel('com.avazbek.nemo_app/accessibility');
  static const _screenshot = MethodChannel('com.avazbek.nemo_app/screenshot');
  static const _notification = MethodChannel('com.avazbek.nemo_app/notification');

  final NemoService _nemo;
  BuildContext? _context; // set by PhoneActionService.startWithContext()
  StreamSubscription? _sub;
  CameraController? _camera;

  PhoneActionService(this._nemo);

  void start() {
    _sub = _nemo.actions.listen(_execute);
  }

  void setContext(BuildContext ctx) => _context = ctx;

  Future<void> _execute(Map<String, dynamic> data) async {
    final id = data['id'] as String? ?? '';
    final cmd = (data['command'] as String? ?? '').trim();
    if (id.isEmpty || cmd.isEmpty) return;

    // Show "Remote control active" notification for non-status commands
    final verb = cmd.split(' ').first.toLowerCase();
    if (verb != 'status' && verb != 'list_apps') {
      await _notification.invokeMethod('showRemoteControl').catchError((_) {});
    }

    // Biometric gate for sensitive actions
    if (BiometricService.isSensitive(cmd) && _context != null && _context!.mounted) {
      final ok = await BiometricService.authenticate(
        _context!,
        'Nemo wants to: $cmd',
      );
      if (!ok) {
        _nemo.sendActionResult(id, ok: false, error: 'user denied — biometric failed');
        return;
      }
    }

    debugPrint('[phone_action] executing: $cmd (id=$id)');

    try {
      final parts = cmd.split(' ');
      final verb = parts[0].toLowerCase();

      switch (verb) {
        case 'status':
          final accessible = await _isAccessibilityEnabled();
          _nemo.sendActionResult(id, text: 'connected | accessibility=$accessible');

        case 'screenshot':
          final b64 = await _takeScreenshot();
          if (b64 != null) {
            _nemo.sendActionResult(id, imageB64: b64);
          } else {
            _nemo.sendActionResult(id, ok: false, error: 'screenshot failed — grant MediaProjection permission');
          }

        case 'camera':
          final b64 = await _captureCamera();
          if (b64 != null) {
            _nemo.sendActionResult(id, imageB64: b64);
          } else {
            _nemo.sendActionResult(id, ok: false, error: 'camera capture failed');
          }

        case 'ui_tree':
          final tree = await _accessibility.invokeMethod<String>('getUiTree') ?? 'unavailable';
          _nemo.sendActionResult(id, text: tree.length > 8000 ? tree.substring(0, 8000) : tree);

        case 'tap':
          if (parts.length >= 3) {
            final x = double.tryParse(parts[1]) ?? 0;
            final y = double.tryParse(parts[2]) ?? 0;
            final ok = await _accessibility.invokeMethod<bool>('tap', {'x': x, 'y': y}) ?? false;
            _nemo.sendActionResult(id, ok: ok, text: ok ? 'tapped $x,$y' : null, error: ok ? null : 'tap failed');
          } else {
            _nemo.sendActionResult(id, ok: false, error: 'tap requires x y coordinates');
          }

        case 'swipe':
          if (parts.length >= 5) {
            final ok = await _accessibility.invokeMethod<bool>('swipe', {
              'x1': double.tryParse(parts[1]) ?? 0,
              'y1': double.tryParse(parts[2]) ?? 0,
              'x2': double.tryParse(parts[3]) ?? 0,
              'y2': double.tryParse(parts[4]) ?? 0,
            }) ?? false;
            _nemo.sendActionResult(id, ok: ok);
          } else {
            _nemo.sendActionResult(id, ok: false, error: 'swipe requires x1 y1 x2 y2');
          }

        case 'type':
          final text = parts.skip(1).join(' ');
          final ok = await _accessibility.invokeMethod<bool>('typeText', {'text': text}) ?? false;
          _nemo.sendActionResult(id, ok: ok, error: ok ? null : 'no focused input field');

        case 'press':
          final button = parts.length > 1 ? parts[1] : '';
          final ok = await _accessibility.invokeMethod<bool>('pressButton', {'button': button}) ?? false;
          _nemo.sendActionResult(id, ok: ok);

        case 'open':
          final pkg = parts.skip(1).join(' ');
          await _openApp(pkg);
          _nemo.sendActionResult(id, text: 'launched $pkg');

        case 'list_apps':
          final apps = await _listApps();
          _nemo.sendActionResult(id, text: apps.join('\n'));

        default:
          _nemo.sendActionResult(id, ok: false, error: 'unknown command: $verb');
      }
    } catch (e) {
      debugPrint('[phone_action] error for $cmd: $e');
      _nemo.sendActionResult(id, ok: false, error: e.toString());
    }
  }

  Future<bool> _isAccessibilityEnabled() async {
    try {
      return await _accessibility.invokeMethod<bool>('isEnabled') ?? false;
    } catch (_) {
      return false;
    }
  }

  Future<String?> _takeScreenshot() async {
    try {
      final bytes = await _screenshot.invokeMethod<Uint8List>('capture');
      if (bytes == null) return null;
      return base64Encode(bytes);
    } catch (e) {
      debugPrint('screenshot error: $e');
      return null;
    }
  }

  Future<String?> _captureCamera() async {
    try {
      final cameras = await availableCameras();
      if (cameras.isEmpty) return null;
      _camera ??= CameraController(cameras.first, ResolutionPreset.medium);
      if (!_camera!.value.isInitialized) await _camera!.initialize();
      final file = await _camera!.takePicture();
      final bytes = await file.readAsBytes();
      return base64Encode(bytes);
    } catch (e) {
      debugPrint('camera error: $e');
      return null;
    }
  }

  Future<void> _openApp(String packageName) async {
    const channel = MethodChannel('com.avazbek.nemo_app/intents');
    await channel.invokeMethod('openApp', {'package': packageName});
  }

  Future<List<String>> _listApps() async {
    try {
      const channel = MethodChannel('com.avazbek.nemo_app/intents');
      final result = await channel.invokeMethod<List>('listApps');
      return result?.cast<String>() ?? [];
    } catch (_) {
      return [];
    }
  }

  void dispose() {
    _sub?.cancel();
    _camera?.dispose();
  }
}
