import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'nemo_service.dart';
import 'phone_command_executor.dart';

/// Listens for action commands from the text/Telegram backend and executes
/// them via the shared PhoneCommandExecutor. Results go back over the engine
/// websocket via NemoService.sendActionResult().
class PhoneActionService {
  static const _notification =
      MethodChannel('com.avazbek.nemo_app/notification');

  final NemoService _nemo;
  final PhoneCommandExecutor _executor;
  final BuildContext? Function() _contextProvider;
  StreamSubscription? _sub;

  PhoneActionService(this._nemo, this._executor, this._contextProvider);

  void start() {
    _sub = _nemo.actions.listen(_execute);
  }

  Future<void> _execute(Map<String, dynamic> data) async {
    final id = data['id'] as String? ?? '';
    final cmd = (data['command'] as String? ?? '').trim();
    if (id.isEmpty || cmd.isEmpty) return;

    // Show "Remote control active" notification for non-status commands
    final verb = cmd.split(' ').first.toLowerCase();
    if (verb != 'status' && verb != 'list_apps') {
      await _notification.invokeMethod('showRemoteControl').catchError((_) {});
    }

    debugPrint('[phone_action] executing: $cmd (id=$id)');
    final r = await _executor.execute(cmd, context: _contextProvider());
    _nemo.sendActionResult(id,
        ok: r.ok, text: r.text, error: r.error, imageB64: r.imageB64);
  }

  void dispose() {
    _sub?.cancel();
    _executor.dispose();
  }
}
