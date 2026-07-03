# Design — Translator mode & Nemo voice guidance (+ four future features)

Approved plan of record, 2026-07-03. Implement translator + guidance now;
the four Tier-next features are designed here for Avazbek to pick from later.

## 1. Translator mode

**What it is.** A spoken toggle that turns Nemo into a strict two-way
interpreter: Avazbek ↔ a foreign-language speaker, both hearing their own
language. Nemo NEVER adds his own content while interpreting — no opinions,
no summaries, no answering the foreign speaker's questions himself.

**The one exception (by design, from Avazbek):** an *aside*. When Avazbek
addresses Nemo by name mid-session ("Nemo, is that price normal?"), Nemo
answers HIM directly, in his language, and does NOT translate that exchange
to the other person. Then interpreting resumes. Detection = speaker gate says
OWNER **and** the utterance is addressed (ambient.is_addressed).

**Direction rules (two signals, either suffices):**
1. *Language*: speech in the target language → render in Avazbek's language;
   speech in Avazbek's language → render in the target language.
2. *Voiceprint*: OWNER verdict → outbound (to target language); non-OWNER →
   inbound (to Avazbek). Used as a hint injected per turn, so a bilingual
   sentence can't flip the direction wrongly.

**Mechanics.**
- `translator.py`: toggle intents ("translator mode [for] chinese",
  "tarjimon rejimi", off: "stop translating / tarjima tugadi"), target-language
  parsing, hint-building. State on `_SessionCtx.translator_lang` (None = off).
- On toggle ON: pump sends `session.update` with interpreter instructions
  (replaces persona for the session) and `turn_detection.create_response=false`
  — the same controlled-response mechanism ambient mode uses, so nothing is
  spoken before we attach the direction hint.
- Per turn: pump injects one context item —
  `[OWNER speaking → translate into <target>]` /
  `[OTHER speaker → translate into Avazbek's language]` /
  `[ASIDE — Avazbek is asking YOU; answer him briefly in his language, do not
  translate this]` — then `response.create`.
- Tools disabled while interpreting (except during asides): tool calls refused
  in `_maybe_tool` when translator is on and the turn isn't an aside.
- OFF: restore instructions (session.update with the normal prompt) and
  `create_response=true`.
- Speaker gate strongly recommended ON (asides + direction hints); language
  rule alone still works when the gate is off.
- Journaling: translated conversations ARE journaled (owner's episodes only,
  as usual) — useful "what did the seller say" recall later.

## 2. Nemo voice guidance (navigation watcher)

**What it is.** "Nemo, navigate me to X" → Nemo speaks turn-by-turn guidance
("500 metrdan keyin, svetofordan o'ngga") woven into the live session, while
the conversation stays fully usable.

**Mechanics.**
- `navigation.py` (server):
  - `start(destination)` → geocode + route via OSRM public HTTP API
    (`OSRM_URL`, default `https://router.project-osrm.org`; no key needed;
    `NAV_PROVIDER=google` + key later). Stores an ordered list of Step
    (lat, lon, instruction, road) per session.
  - `on_location(lat, lon)` → distance to next maneuver (haversine);
    announcement ladder per step: ~500m ("in 500 meters, …"), ~100m
    ("soon, …"), ≤30m ("now: turn right"); each rung announced once.
    Arrival (≤40m to final step) → "you've arrived" + auto-stop.
  - Off-route: >80m from the step line for 3 consecutive fixes → re-fetch
    route from current position, announce "rerouting".
- Voice tools (capabilities entry `navigation`): `start_navigation(destination)`,
  `stop_navigation()`. TIER0 — instant.
- Transport: the app streams `{"type": "location", "lat", "lon"}` frames over
  the EXISTING authenticated voice websocket every ~4s while nav is active.
  `_recv_client` routes them to the session's Navigator; announcements go out
  through `link.inject_text_when_idle` (same rail as background tool results,
  so guidance interleaves politely with conversation).
- App side (Dart): `location_streamer.dart` — starts on `nav_start` server
  event, stops on `nav_stop`; `geolocator` for fixes. Foreground-service
  behavior rides the existing voice session. (On-device testing next phone
  session; code compiles under `flutter analyze`.)
- No Google dependency by default; OSRM instructions are English → Nemo
  renders them naturally in whatever language the conversation is in (the
  inject text says "say this naturally in the conversation language").

**Privacy/security:** location frames only accepted on the authenticated ws;
never journaled; Navigator state is in-memory per session and dies with it.

## 3. Future features — ALL BUILT 2026-07-03 (except custom voice: ops-only)

### Voice ledger ✅ built — ledger.py + tests + harness phase 12
"50 ming tushlikka ketdi" → `ledger` table in memory_v2.db
(ts, amount, currency, category, note); `log_expense`/`log_habit` tools +
TIER2 patterns; weekly spoken summary via the briefing; "how much did I spend
on food this month" answered from SQL, not the LLM's imagination.

### Multi-speaker memory ✅ built — speaker_gate guests + enroll_speaker + attribution + harness phase 13
`voiceprints.json` grows named profiles (enroll: "Nemo, remember Aziz's
voice"). speaker_gate returns (verdict, name). Episodes gain a `speaker`
column; meeting transcripts + ambient turns attributed by name; recall can
answer "what did Aziz say about the trip". Strangers still store nothing.
Protected tools stay OWNER-only — named guests get attribution, not access.

### Learning coach ✅ built — study_coach.py (SM-2) + study watcher + tests
`study_items` table (front, back, due_ts, ease) — SM-2 spaced repetition.
Feeds from the study-assistant skill's flashcards + explicit "quiz me on
this". A proactive watcher offers due cards at good moments (interrupt
policy applies; low severity). Progress stats in the weekly briefing.

### Custom voice ⏳ ops-only — OMNI_TTS_VOICE on the ava-gpu TTS sidecar (with M4)
Waits for M4 self-hosting: clone/pick a voice for the TTS side of the
omni gateway (`OMNI_TTS_VOICE`), fine-tuned on a chosen reference. Zero
server-code change — it's a TTS-sidecar model swap on ava-gpu.

## Verification plan (this build)

- Unit: translator toggles/direction/aside; navigation route parsing,
  announcement ladder, arrival, reroute (OSRM mocked).
- Harness: phase 10 translator (owner turn → hint says translate-out; other
  voice → translate-in; aside answered, not translated), phase 11 guidance
  (start nav with mocked route, stream location frames, expect spoken
  maneuvers then arrival).
- Gates: ruff/mypy/lizard/pytest + flutter analyze on new Dart.
