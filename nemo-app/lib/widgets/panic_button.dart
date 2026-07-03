import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../services/nemo_service.dart';
import '../theme.dart';

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
            color: NemoColors.danger.withValues(alpha: 0.18),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: NemoColors.danger.withValues(alpha: 0.5)),
          ),
          child: const Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.stop_circle_outlined, color: Colors.white, size: 16),
            SizedBox(width: 4),
            Text('STOP',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 11,
                  fontWeight: FontWeight.bold,
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
        title: const Text('Stop all control?'),
        content: const Text(
          'This will immediately stop Nemo from controlling your phone and disconnect the session.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
                backgroundColor: NemoColors.danger,
                foregroundColor: NemoColors.bg),
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
          content: Text('Nemo phone control stopped'),
          backgroundColor: NemoColors.danger,
          duration: Duration(seconds: 3),
        ),
      );
    }
  }
}
