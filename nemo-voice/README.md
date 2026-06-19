# Nemo Voice

Real-time voice for Nemo. The Android app streams your microphone to this
server, which talks to Deepgram's Voice Agent (or Gemini Live) and streams
Nemo's spoken reply back. Say "hey nemo" anywhere — even inside another app —
and just talk.

## What it can do

- Natural back-and-forth conversation with barge-in (interrupt him mid-sentence).
- Shares the same memory as the text/Telegram Nemo (`remember` / `recall`).
- Controls your phone by voice:
  - **open_app** — "open Spotify" (any installed app, by name)
  - **set_alarm / set_timer** — "set an alarm for 2am" (uses your clock app)
  - **message_contact** — "message Aziz on Telegram: do you have time today"
    (finds the contact in your phone, sends through your own Telegram)
  - **phone_command** — advanced screen control (read screen, tap, type)
- Voice: Deepgram Aura-2 "Draco" (deep British male — Jarvis style).
  Change with `DEEPGRAM_SPEAK_MODEL` in `.env`.

## Parts

```
server/
├── streaming_service.py  # WebSocket bridge: app ↔ Deepgram (port 3002)
├── gemini_streaming.py   # Alternative backend: Gemini Live (VOICE_BACKEND=gemini)
├── voice_brain.py        # Nemo's identity, shared memory, tools
├── phone_tools.py        # Phone-control tool definitions
└── action_bridge.py      # Sends phone commands to the app, waits for results
client/                   # Browser test client (port 3001)
```

## Setup

In the repo root `.env`:

```
NEMO_APP_TOKEN=...        # same token the app uses
DEEPGRAM_API_KEY=...      # or GEMINI_API_KEY with VOICE_BACKEND=gemini
```

Optional TLS (recommended — see `scripts/gen_server_cert.sh` in the repo root):

```
VOICE_TLS_CERT=/path/nemo-server.crt
VOICE_TLS_KEY=/path/nemo-server.key
```

Then:

```bash
cd server
pip install -r requirements.txt
python streaming_service.py
```

The app connects to port 3002 automatically using the Server URL from its
settings (use `wss://` once TLS is on).

## Cost notes

The session prompt is small and sent once per session, memory is fetched
on demand through tools, and Nemo is instructed to answer in 1-2 sentences —
long replies are billed twice (LLM tokens + TTS characters). The wake word
runs fully on-device (Vosk), costing nothing.
