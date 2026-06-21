import 'dart:async';
import 'dart:convert';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';

/// Live camera "vision mode" — a full-screen preview that stays warm so Nemo can
/// grab the CURRENT frame the instant you ask ("what is this?"). The voice
/// session keeps running underneath; this screen only owns the camera.
///
/// Privacy + lifecycle (all load-bearing, from review):
/// - `enableAudio: false` — the voice session owns the mic; the camera must NOT
///   grab a second AudioRecord or it kills the conversation.
/// - This screen is the SINGLE owner of the CameraController; it disposes on
///   teardown and on app background (a live camera must never run backgrounded).
/// - Auto-closes after 30s with no grab.
class VisionMode {
  static _VisionModeScreenState? _active;

  static bool get isOpen =>
      _active != null && _active!._ready && _active!.mounted;

  /// Open the live camera and wait until it's ready (bounded — never hangs the
  /// action that triggered it).
  static Future<void> open(BuildContext context) async {
    if (isOpen) return;
    final ready = Completer<void>();
    Navigator.of(context).push(
      MaterialPageRoute(
        fullscreenDialog: true,
        builder: (_) => VisionModeScreen(
          onReady: () {
            if (!ready.isCompleted) ready.complete();
          },
        ),
      ),
    );
    await ready.future.timeout(const Duration(seconds: 8), onTimeout: () {});
  }

  /// Grab the current frame as base64 JPEG, or null if not ready.
  static Future<String?> grab() async => _active?._grab();
}

class VisionModeScreen extends StatefulWidget {
  final VoidCallback? onReady;
  const VisionModeScreen({super.key, this.onReady});

  @override
  State<VisionModeScreen> createState() => _VisionModeScreenState();
}

class _VisionModeScreenState extends State<VisionModeScreen>
    with WidgetsBindingObserver {
  CameraController? _controller;
  bool _ready = false;
  Timer? _idle;
  CameraLensDirection _lens = CameraLensDirection.back;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    VisionMode._active = this;
    _init();
  }

  Future<void> _init() async {
    try {
      final cams = await availableCameras();
      if (cams.isEmpty) {
        widget.onReady?.call();
        _close();
        return;
      }
      final cam = cams.firstWhere(
        (c) => c.lensDirection == _lens,
        orElse: () => cams.first,
      );
      final c = CameraController(
        cam,
        ResolutionPreset.medium,
        enableAudio: false, // P0: never take the mic — voice owns it
      );
      await c.initialize();
      if (!mounted) {
        await c.dispose();
        return;
      }
      setState(() {
        _controller = c;
        _ready = true;
      });
      _resetIdle();
      widget.onReady?.call();
    } catch (_) {
      widget.onReady?.call(); // unblock the opener even on failure
      _close();
    }
  }

  /// Flip between back and front camera, re-initializing the controller.
  Future<void> _flip() async {
    _lens = _lens == CameraLensDirection.back
        ? CameraLensDirection.front
        : CameraLensDirection.back;
    final old = _controller;
    setState(() {
      _ready = false;
      _controller = null;
    });
    await old?.dispose();
    if (mounted) await _init();
  }

  Future<String?> _grab() async {
    final c = _controller;
    if (c == null || !_ready || !c.value.isInitialized) return null;
    _resetIdle();
    try {
      final file = await c.takePicture();
      return base64Encode(await file.readAsBytes());
    } catch (_) {
      return null;
    }
  }

  void _resetIdle() {
    _idle?.cancel();
    _idle = Timer(const Duration(seconds: 30), _close);
  }

  void _close() {
    if (mounted) Navigator.of(context).maybePop();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Privacy: a live camera must not run while backgrounded. Dispose the sensor
    // SYNCHRONOUSLY (the route pop is async — that alone would leave the camera
    // live for a frame or two), then close the screen.
    if (state == AppLifecycleState.inactive ||
        state == AppLifecycleState.paused) {
      _idle?.cancel();
      _ready = false;
      final c = _controller;
      _controller = null;
      c?.dispose();
      _close();
    }
  }

  @override
  void dispose() {
    _idle?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    VisionMode._active = null;
    _controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        children: [
          if (_ready && _controller != null)
            Positioned.fill(child: CameraPreview(_controller!))
          else
            const Center(child: CircularProgressIndicator()),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Row(
                children: [
                  const Icon(Icons.fiber_manual_record,
                      color: Colors.redAccent, size: 14),
                  const SizedBox(width: 6),
                  const Text('Nemo is looking',
                      style: TextStyle(color: Colors.white)),
                  const Spacer(),
                  IconButton(
                    tooltip: 'Flip camera',
                    icon: const Icon(Icons.flip_camera_ios, color: Colors.white),
                    onPressed: _ready ? _flip : null,
                  ),
                  IconButton(
                    icon: const Icon(Icons.close, color: Colors.white),
                    onPressed: _close,
                  ),
                ],
              ),
            ),
          ),
          const SafeArea(
            child: Align(
              alignment: Alignment.bottomCenter,
              child: Padding(
                padding: EdgeInsets.only(bottom: 28),
                child: Text(
                  'Point at something and ask "what is this?"',
                  style: TextStyle(color: Colors.white70),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
