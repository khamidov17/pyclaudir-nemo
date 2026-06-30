import 'dart:async';
import 'dart:convert';
import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter/foundation.dart';
import 'biometric_service.dart';
import 'messages_service.dart';
import '../screens/vision_mode_screen.dart';

/// Result of one phone command.
class ActionOutcome {
  final bool ok;
  final String? text;
  final String? error;
  final String? imageB64;
  const ActionOutcome.success([this.text])
      : ok = true, error = null, imageB64 = null;
  const ActionOutcome.image(this.imageB64)
      : ok = true, text = null, error = null;
  const ActionOutcome.fail(this.error)
      : ok = false, text = null, imageB64 = null;
}

/// Executes phone commands ("open spotify", "set_alarm 7 30", "tg_msg Aziz|hi")
/// on behalf of BOTH backends: the text/Telegram Nemo (via NemoService) and
/// the voice agent (via VoiceSessionController). Sensitive verbs are gated by
/// system biometrics and FAIL CLOSED when no UI is available to confirm.
class PhoneCommandExecutor {
  static const _accessibility =
      MethodChannel('com.avazbek.nemo_app/accessibility');
  static const _screenshot = MethodChannel('com.avazbek.nemo_app/screenshot');
  static const _intents = MethodChannel('com.avazbek.nemo_app/intents');

  Future<ActionOutcome> execute(String cmd, {BuildContext? context}) async {
    if (BiometricService.isSensitive(cmd)) {
      if (context == null || !context.mounted) {
        return const ActionOutcome.fail(
            'locked — open the Nemo app to confirm this action');
      }
      final ok = await BiometricService.authenticate(context, 'Nemo wants to: $cmd');
      if (!ok) return const ActionOutcome.fail('user denied — biometric failed');
    }
    try {
      return await _dispatch(cmd, context: context)
          .timeout(const Duration(seconds: 15),
              onTimeout: () => ActionOutcome.fail('command timed out: $cmd'));
    } catch (e) {
      debugPrint('[executor] error for $cmd: $e');
      return ActionOutcome.fail(e.toString());
    }
  }

  Future<ActionOutcome> _dispatch(String cmd, {BuildContext? context}) async {
    final parts = cmd.trim().split(RegExp(r'\s+'));
    final verb = parts[0].toLowerCase();
    final arg = parts.skip(1).join(' ');
    switch (verb) {
      case 'status':
        final accessible = await _isAccessibilityEnabled();
        return ActionOutcome.success('connected | accessibility=$accessible');
      case 'screenshot':
        return _takeScreenshot();
      case 'camera':
        return _captureCamera(context: context);
      case 'read_messages':
        // Return the captured-notification buffer as a JSON string in `text`
        // (action_bridge only forwards specific fields; the server parses text).
        if (!await MessageAwareness.isAccessGranted()) {
          return const ActionOutcome.fail(
              'notification access is off — turn it on in Nemo Settings');
        }
        return ActionOutcome.success(await MessageAwareness.getMessages());
      case 'ui_tree':
        final tree =
            await _accessibility.invokeMethod<String>('getUiTree') ?? 'unavailable';
        return ActionOutcome.success(
            tree.length > 8000 ? tree.substring(0, 8000) : tree);
      case 'tap':
        return _tap(parts);
      case 'swipe':
        return _swipe(parts);
      case 'type':
        final ok =
            await _accessibility.invokeMethod<bool>('typeText', {'text': arg}) ??
                false;
        return ok
            ? const ActionOutcome.success('typed')
            : const ActionOutcome.fail('no focused input field');
      case 'press':
        final ok = await _accessibility
                .invokeMethod<bool>('pressButton', {'button': arg}) ??
            false;
        return ok
            ? const ActionOutcome.success('pressed')
            : const ActionOutcome.fail('press failed');
      case 'open':
        return _openApp(arg);
      case 'list_apps':
        final apps =
            await _intents.invokeMethod<List>('listAppsWithLabels') ?? [];
        return ActionOutcome.success(apps.whereType<String>().join('\n'));
      case 'set_alarm':
        return _setAlarm(parts);
      case 'set_timer':
        return _setTimer(parts);
      case 'tg_msg':
        return _telegramMessage(arg);
      default:
        return ActionOutcome.fail('unknown command: $verb');
    }
  }

  Future<ActionOutcome> _tap(List<String> parts) async {
    if (parts.length < 3) {
      return const ActionOutcome.fail('tap requires x y coordinates');
    }
    final ok = await _accessibility.invokeMethod<bool>('tap', {
          'x': double.tryParse(parts[1]) ?? 0,
          'y': double.tryParse(parts[2]) ?? 0,
        }) ??
        false;
    return ok
        ? ActionOutcome.success('tapped ${parts[1]},${parts[2]}')
        : const ActionOutcome.fail('tap failed');
  }

  Future<ActionOutcome> _swipe(List<String> parts) async {
    if (parts.length < 5) {
      return const ActionOutcome.fail('swipe requires x1 y1 x2 y2');
    }
    final ok = await _accessibility.invokeMethod<bool>('swipe', {
          'x1': double.tryParse(parts[1]) ?? 0,
          'y1': double.tryParse(parts[2]) ?? 0,
          'x2': double.tryParse(parts[3]) ?? 0,
          'y2': double.tryParse(parts[4]) ?? 0,
        }) ??
        false;
    return ok
        ? const ActionOutcome.success('swiped')
        : const ActionOutcome.fail('swipe failed');
  }

  /// Open by human name ("Spotify", "gallery") or exact package id.
  Future<ActionOutcome> _openApp(String nameOrPkg) async {
    final q = nameOrPkg.trim();
    if (q.isEmpty) return const ActionOutcome.fail('open requires an app name');
    final looksLikePackage = q.contains('.') && !q.contains(' ');
    if (looksLikePackage) {
      final ok =
          await _intents.invokeMethod<bool>('openApp', {'package': q}) ?? false;
      if (ok) return ActionOutcome.success('launched $q');
    }
    final label =
        await _intents.invokeMethod<String>('openAppByName', {'name': q});
    if (label != null) return ActionOutcome.success('opened $label');
    return ActionOutcome.fail('no installed app matching "$q"');
  }

  Future<ActionOutcome> _setAlarm(List<String> parts) async {
    if (parts.length < 2) {
      return const ActionOutcome.fail('set_alarm requires: hour [minutes] [label]');
    }
    final hour = int.tryParse(parts[1]);
    if (hour == null || hour < 0 || hour > 23) {
      return const ActionOutcome.fail('hour must be 0-23');
    }
    final minutes = parts.length > 2 ? (int.tryParse(parts[2]) ?? 0) : 0;
    if (minutes < 0 || minutes > 59) {
      return const ActionOutcome.fail('minutes must be 0-59');
    }
    final label = parts.skip(3).join(' ');
    final ok = await _intents.invokeMethod<bool>('setAlarm', {
          'hour': hour,
          'minutes': minutes,
          'message': label.isEmpty ? 'Nemo' : label,
        }) ??
        false;
    final hh = hour.toString().padLeft(2, '0');
    final mm = minutes.toString().padLeft(2, '0');
    return ok
        ? ActionOutcome.success('alarm set for $hh:$mm')
        : const ActionOutcome.fail('could not set alarm — no clock app?');
  }

  Future<ActionOutcome> _setTimer(List<String> parts) async {
    final seconds = parts.length > 1 ? int.tryParse(parts[1]) : null;
    if (seconds == null || seconds <= 0) {
      return const ActionOutcome.fail('set_timer requires seconds');
    }
    final label = parts.skip(2).join(' ');
    final ok = await _intents.invokeMethod<bool>('setTimer', {
          'seconds': seconds,
          'message': label.isEmpty ? 'Nemo timer' : label,
        }) ??
        false;
    return ok
        ? ActionOutcome.success('timer set for ${seconds}s')
        : const ActionOutcome.fail('could not set timer — no clock app?');
  }

  /// "tg_msg Aziz|do u have time today" — open Telegram, use ITS OWN search to
  /// find the person by name (resolves via Telegram's first/last name + @user,
  /// no contacts permission), open the top result, type the message, tap Send.
  Future<ActionOutcome> _telegramMessage(String arg) async {
    final sep = arg.indexOf('|');
    if (sep <= 0) {
      return const ActionOutcome.fail('usage: tg_msg <name>|<message>');
    }
    final name = arg.substring(0, sep).trim();
    final msg = arg.substring(sep + 1).trim();
    if (name.isEmpty || msg.isEmpty) {
      return const ActionOutcome.fail('need both a name and a message');
    }
    if (!await _isAccessibilityEnabled()) {
      return const ActionOutcome.fail(
          'enable the Nemo accessibility service first (Settings → '
          'Accessibility → Nemo) so I can drive Telegram');
    }

    // Remember what was on screen so we can hand the phone back after sending
    // (the user asked to message someone, not to land in Telegram).
    final prior = await _foregroundPackage();

    final telegramLabel = await _intents.invokeMethod<String>(
        'openAppByName', {'name': 'Telegram'});
    if (telegramLabel == null) {
      return const ActionOutcome.fail('Telegram is not installed');
    }
    await Future.delayed(const Duration(milliseconds: 2500));

    // Open Telegram's search (magnifier — contentDescription "Search").
    if (!await _click('Search')) {
      return const ActionOutcome.fail(
          "couldn't find Telegram's search — is this the chats screen?");
    }
    await Future.delayed(const Duration(milliseconds: 800));

    // Type the name into the now-focused search box, let results load, open
    // the top hit.
    if (!await _type(name)) {
      return const ActionOutcome.fail('could not type into Telegram search');
    }
    await Future.delayed(const Duration(milliseconds: 1600));
    final hit = await _accessibility.invokeMethod<bool>('clickFirstResult') ?? false;
    if (!hit) {
      return ActionOutcome.fail('no Telegram chat found for "$name"');
    }
    await Future.delayed(const Duration(milliseconds: 1800));

    // Type into the message box (the chat's EditText) and send.
    if (!await _type(msg)) {
      return ActionOutcome.fail(
          'opened the chat but could not type the message');
    }
    // Retry loop: link previews delay the Send button by up to 1s
    bool sent = false;
    for (int i = 0; i < 3 && !sent; i++) {
      await Future.delayed(const Duration(milliseconds: 400));
      sent = await _click('Send') || await _click('Send message');
    }
    if (!sent) {
      // Leave them in Telegram so they can tap send themselves.
      return ActionOutcome.success(
          'typed the message to $name — tap send to confirm');
    }
    await _returnTo(prior);
    return ActionOutcome.success('sent to $name on Telegram');
  }

  /// The app currently in the foreground (best-effort, via accessibility).
  Future<String?> _foregroundPackage() async {
    try {
      return await _accessibility.invokeMethod<String>('getForegroundPackage');
    } catch (_) {
      return null;
    }
  }

  /// Best-effort hand-back: re-open whatever app was in front before Nemo took
  /// over. Never throws — if it fails the user just stays in Telegram. Skips
  /// the hand-back when they were already in Telegram (or it's unknown).
  Future<void> _returnTo(String? pkg) async {
    if (pkg == null || pkg.isEmpty || pkg.contains('telegram')) return;
    try {
      await _intents.invokeMethod<bool>('openApp', {'package': pkg});
    } catch (_) {}
  }

  Future<bool> _click(String query) async =>
      await _accessibility
          .invokeMethod<bool>('clickByText', {'query': query})
          .timeout(const Duration(seconds: 15), onTimeout: () => false) ??
      false;

  Future<bool> _type(String text) async =>
      await _accessibility
          .invokeMethod<bool>('typeText', {'text': text})
          .timeout(const Duration(seconds: 15), onTimeout: () => false) ??
      false;

  Future<bool> _isAccessibilityEnabled() async {
    try {
      return await _accessibility.invokeMethod<bool>('isEnabled') ?? false;
    } catch (_) {
      return false;
    }
  }

  Future<ActionOutcome> _takeScreenshot() async {
    try {
      final bytes = await _screenshot.invokeMethod<Uint8List>('capture');
      if (bytes == null) {
        return const ActionOutcome.fail(
            'screenshot failed — grant MediaProjection permission');
      }
      return ActionOutcome.image(base64Encode(bytes));
    } catch (e) {
      return ActionOutcome.fail('screenshot failed: $e');
    }
  }

  Future<ActionOutcome> _captureCamera({BuildContext? context}) async {
    // Prefer the warm, on-screen live camera (vision mode): instant grab of the
    // exact frame the user sees.
    if (VisionMode.isOpen) {
      final b64 = await VisionMode.grab();
      if (b64 != null) return ActionOutcome.image(b64);
    }
    // Not open yet → open the live camera screen, wait for it, grab THAT frame.
    if (context != null && context.mounted) {
      await VisionMode.open(context);
      // Re-check mounted: VisionMode.open is async and context may be gone.
      if (context.mounted && VisionMode.isOpen) {
        final b64 = await VisionMode.grab();
        if (b64 != null) return ActionOutcome.image(b64);
      }
    }
    // Cold fallback (no UI context): one-shot, mic-free (never take the mic — the
    // voice session owns it), created and disposed locally.
    CameraController? c;
    try {
      final cameras = await availableCameras();
      if (cameras.isEmpty) return const ActionOutcome.fail('no camera');
      c = CameraController(cameras.first, ResolutionPreset.medium,
          enableAudio: false);
      await c.initialize();
      final file = await c.takePicture();
      return ActionOutcome.image(base64Encode(await file.readAsBytes()));
    } on CameraException catch (e) {
      final desc = (e.description ?? '').toLowerCase();
      if (desc.contains('in use') || desc.contains('camerainuse') || desc.contains('busy')) {
        return const ActionOutcome.fail(
            'camera is in use by another app — close it first');
      }
      return ActionOutcome.fail('camera failed: ${e.description}');
    } catch (e) {
      return ActionOutcome.fail('camera capture failed: $e');
    } finally {
      await c?.dispose();
    }
  }

  void dispose() {}
}
