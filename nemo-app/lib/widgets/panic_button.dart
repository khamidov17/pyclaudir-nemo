import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../services/nemo_service.dart';

/// One-tap emergency stop for all Nemo phone control.
/// Sends panic signal to server, disconnects WebSocket.
class PanicButton extends StatelessWidget {
  const PanicButton({super.key});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onLongPress: () => _trigger(context),
      child: Tooltip(
        message: 'Long press to stop all Nemo control',
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(
            color: Colors.red.shade900.withOpacity(0.8),
            borderRadius: BorderRadius.circular(12),
          ),
          child: const Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.stop_circle_outlined, color: Colors.white, size: 16),
            SizedBox(width: 4),
            Text('STOP', style: TextStyle(
              color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold,
              letterSpacing: 1.2,
            )),
          ]),
        ),
      ),
    );
  }

  Future<void> _trigger(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: const Color(0xFF1E1E2E),
        title: const Text('Stop all control?',
            style: TextStyle(color: Colors.white)),
        content: const Text(
          'This will immediately stop Nemo from controlling your phone and disconnect the session.',
          style: TextStyle(color: Colors.white70),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Stop everything'),
          ),
        ],
      ),
    );

    if (confirmed == true && context.mounted) {
      final nemo = context.read<NemoService>();
      // Send panic signal to server
      nemo.sendPanic();
      // Show confirmation
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('✓ Nemo phone control stopped'),
          backgroundColor: Colors.red,
          duration: Duration(seconds: 3),
        ),
      );
    }
  }
}
