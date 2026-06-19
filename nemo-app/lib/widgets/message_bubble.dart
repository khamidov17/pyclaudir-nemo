import 'dart:io';
import 'package:flutter/material.dart';
import '../models/message.dart';
import '../theme.dart';

class MessageBubble extends StatelessWidget {
  final Message message;
  const MessageBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    final isNemo = message.sender == Sender.nemo;
    final primary = Theme.of(context).colorScheme.primary;
    // Text on a white Nemo bubble is dark; on a blue user bubble it's white.
    final fg = isNemo ? NemoColors.text : Colors.white;
    final fgDim = isNemo ? NemoColors.textDim : Colors.white70;

    return Align(
      alignment: isNemo ? Alignment.centerLeft : Alignment.centerRight,
      child: Container(
        margin: EdgeInsets.only(
          left: isNemo ? 12 : 60,
          right: isNemo ? 60 : 12,
          top: 3,
          bottom: 3,
        ),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: isNemo ? NemoColors.surface : primary,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(18),
            topRight: const Radius.circular(18),
            bottomLeft: Radius.circular(isNemo ? 4 : 18),
            bottomRight: Radius.circular(isNemo ? 18 : 4),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (message.type == MessageType.image && message.mediaPath != null)
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.file(
                  File(message.mediaPath!),
                  width: 220,
                  fit: BoxFit.cover,
                ),
              ),
            if (message.type == MessageType.file)
              Row(children: [
                Icon(Icons.insert_drive_file, color: fgDim, size: 20),
                const SizedBox(width: 6),
                Expanded(
                    child: Text(
                  message.mediaName ?? 'File',
                  style: TextStyle(color: fgDim, fontSize: 13),
                  overflow: TextOverflow.ellipsis,
                )),
              ]),
            if (message.type == MessageType.recording)
              Row(children: [
                const Icon(Icons.mic, color: Colors.redAccent, size: 18),
                const SizedBox(width: 6),
                Text(
                  message.text.length > 80
                      ? '${message.text.substring(0, 80)}…'
                      : message.text,
                  style: TextStyle(color: fgDim, fontSize: 13),
                ),
              ]),
            if (message.text.isNotEmpty &&
                message.type != MessageType.recording) ...[
              if (message.type == MessageType.image) const SizedBox(height: 6),
              Text(
                message.text,
                style: TextStyle(color: fg, fontSize: 15),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
