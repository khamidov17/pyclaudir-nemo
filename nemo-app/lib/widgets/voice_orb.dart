import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../theme.dart';

/// The state the orb renders. Drives color + animation energy.
enum OrbState { idle, listening, speaking, connecting }

/// The JARVIS voice orb — concentric glowing rings with a soft bloom that
/// breathes. Alive and high-end: idle calmly pulses, listening glows bright,
/// speaking shifts to a warmer teal with faster motion.
class VoiceOrb extends StatefulWidget {
  final OrbState state;
  final double size;
  final VoidCallback? onTap;

  const VoiceOrb({
    super.key,
    required this.state,
    this.size = 240,
    this.onTap,
  });

  @override
  State<VoiceOrb> createState() => _VoiceOrbState();
}

class _VoiceOrbState extends State<VoiceOrb>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl =
      AnimationController(vsync: this, duration: const Duration(seconds: 4))
        ..repeat();

  Color get _accent => switch (widget.state) {
        OrbState.speaking => NemoColors.speaking,
        OrbState.idle => NemoColors.accent2,
        _ => NemoColors.accent,
      };

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: widget.onTap,
      behavior: HitTestBehavior.opaque,
      child: AnimatedBuilder(
        animation: _ctrl,
        builder: (_, __) => CustomPaint(
          size: Size.square(widget.size),
          painter: _OrbPainter(
            t: _ctrl.value,
            accent: _accent,
            state: widget.state,
          ),
        ),
      ),
    );
  }
}

class _OrbPainter extends CustomPainter {
  final double t;
  final Color accent;
  final OrbState state;

  _OrbPainter({required this.t, required this.accent, required this.state});

  double get _energy => switch (state) {
        OrbState.listening => 1.0,
        OrbState.speaking => 1.0,
        OrbState.connecting => 0.5,
        OrbState.idle => 0.35,
      };

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final maxR = size.width / 2;
    final breathe = math.sin(t * 2 * math.pi);
    final energy = _energy;

    // Outer bloom — soft blurred halo.
    final bloomR = maxR * (0.78 + 0.10 * breathe * energy);
    canvas.drawCircle(
      center,
      bloomR,
      Paint()
        ..color = accent.withValues(alpha: 0.18 + 0.12 * energy)
        ..maskFilter = MaskFilter.blur(BlurStyle.normal, 32 + 24 * energy),
    );

    // Concentric rings — phase-shifted so they drift outward.
    for (var i = 0; i < 3; i++) {
      final phase = (t + i / 3) % 1.0;
      final r = maxR * (0.42 + 0.34 * phase);
      final fade = (1.0 - phase) * (0.5 + 0.5 * energy);
      canvas.drawCircle(
        center,
        r,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.5 + 1.5 * energy
          ..color = accent.withValues(alpha: 0.30 * fade),
      );
    }

    // Core disc — a blend of the two blues: bright cornflower center melting
    // into the deep electric blue at the edge.
    final coreR = maxR * (0.40 + 0.04 * breathe * energy);
    final coreRect = Rect.fromCircle(center: center, radius: coreR);
    canvas.drawCircle(
      center,
      coreR,
      Paint()
        ..shader = RadialGradient(
          colors: [
            Color.lerp(NemoColors.accent2, Colors.white, 0.45)!,
            NemoColors.accent2,
            NemoColors.accent,
          ],
          stops: const [0.0, 0.5, 1.0],
        ).createShader(coreRect),
    );

    // Inner glow ring for crisp edge.
    canvas.drawCircle(
      center,
      coreR,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..color = Color.lerp(accent, Colors.white, 0.5)!
            .withValues(alpha: 0.6 + 0.4 * energy),
    );

    // Highlight crescent — gives the orb a glassy, 3D sheen.
    canvas.drawCircle(
      center.translate(-coreR * 0.25, -coreR * 0.28),
      coreR * 0.45,
      Paint()
        ..color = Colors.white.withValues(alpha: 0.12)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 18),
    );
  }

  @override
  bool shouldRepaint(_OrbPainter old) =>
      old.t != t || old.accent != accent || old.state != state;
}
