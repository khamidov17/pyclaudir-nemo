/// Snapshot of voice-session diagnostics for the debug overlay.
class VoiceDebugStats {
  const VoiceDebugStats({
    this.bargeInLatencyMs = 0,
    this.selfBargeCount = 0,
    this.aecEnabled = false,
  });

  final int bargeInLatencyMs;
  final int selfBargeCount;
  final bool aecEnabled;

  VoiceDebugStats copyWith({
    int? bargeInLatencyMs,
    int? selfBargeCount,
    bool? aecEnabled,
  }) =>
      VoiceDebugStats(
        bargeInLatencyMs: bargeInLatencyMs ?? this.bargeInLatencyMs,
        selfBargeCount: selfBargeCount ?? this.selfBargeCount,
        aecEnabled: aecEnabled ?? this.aecEnabled,
      );
}
