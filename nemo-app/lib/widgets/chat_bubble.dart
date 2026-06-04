import 'package:flutter/material.dart';
import '../models/message.dart';

class ChatBubble extends StatelessWidget {
  final Message message;

  const ChatBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    final isNemo = message.sender == Sender.nemo;
    return Align(
      alignment: isNemo ? Alignment.centerLeft : Alignment.centerRight,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4, horizontal: 12),
        padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 14),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.75,
        ),
        decoration: BoxDecoration(
          color: isNemo
              ? const Color(0xFF1E1E2E)
              : Theme.of(context).colorScheme.primary,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(18),
            topRight: const Radius.circular(18),
            bottomLeft: Radius.circular(isNemo ? 4 : 18),
            bottomRight: Radius.circular(isNemo ? 18 : 4),
          ),
        ),
        child: Text(
          message.text,
          style: TextStyle(
            color: isNemo ? Colors.white70 : Colors.white,
            fontSize: 15,
          ),
        ),
      ),
    );
  }
}
