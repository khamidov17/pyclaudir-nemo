/// The Nemo voices the user can choose in Settings. The `id` must be a voice
/// the active backend accepts — these are verified-working voices on
/// qwen3.5-omni-plus-realtime (server whitelist in qwen_realtime.py).
class NemoVoice {
  final String id;
  final String label;
  final String blurb;
  const NemoVoice(this.id, this.label, this.blurb);
}

const kNemoVoices = <NemoVoice>[
  NemoVoice('Ethan', 'Ethan', 'Male — calm, clear'),
  NemoVoice('Ryan', 'Ryan', 'Male — natural'),
  NemoVoice('Aiden', 'Aiden', 'Male — friendly'),
  NemoVoice('Tina', 'Tina', 'Female — clear'),
  NemoVoice('Serena', 'Serena', 'Female — gentle'),
  NemoVoice('Jennifer', 'Jennifer', 'Female — warm'),
];

const kDefaultVoiceId = 'Ethan';
