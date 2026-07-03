# Uzbek & Kazakh Voice — Option B (corrected)
## Qwen3-Omni = ears only. Claude = brain. Qwen3-TTS = voice.

> **Hardware**: 8× A6000 (48 GB each = 384 GB total)
> **Timeline**: Uzbek live Day 4, Kazakh live Day 6
> **Latency**: chitchat ~350 ms TTFSW. Brain tasks = Claude round-trip (same as today, just in Uzbek).
> **Two fine-tuning jobs only.** Omni: ASR. TTS: speech synthesis.

---

## Component contract (non-negotiable, pin this before writing a line of code)

```
┌──────────────────────────────────────────────────────────────────────┐
│ EARS    Qwen3-Omni-30B-A3B                                           │
│         audio in → Uzbek/Kazakh transcript text                      │
│         fine-tune: ASR only (NOT conversation, NOT response gen)     │
├──────────────────────────────────────────────────────────────────────┤
│ BRAIN   Claude engine (unchanged)                                    │
│         tools, memory, reminders, reasoning                          │
│         prompted to respond in detected language (already multilingual)
│         no GPU, no fine-tuning                                       │
├──────────────────────────────────────────────────────────────────────┤
│ VOICE   Qwen3-TTS-12Hz-1.7B                                         │
│         Uzbek/Kazakh text → audio, streaming, ~97 ms first packet   │
│         fine-tune: Uzbek (or use Sayro), Kazakh (new)               │
└──────────────────────────────────────────────────────────────────────┘
```

**Latency honest accounting:**

| Turn type | Path | TTFSW |
|---|---|---|
| Chitchat / quick Q&A | Omni ASR → Claude (fast) → TTS | ~350 ms |
| Reminder / tool / memory | Omni ASR → Claude engine (tool call) → TTS | ~2–5 s (same as today) |

The 350 ms headline is for turns that never touch a tool. Every reminder and task delegation has always paid the Claude round-trip — that doesn't change. The win is those turns now arrive and respond in Uzbek/Kazakh instead of English.

---

## The two fine-tuning jobs

### Job 1 — Qwen3-Omni-30B-A3B: ASR fine-tune (ears)

**Goal**: transcribe Uzbek/Kazakh speech to text accurately.
**Scope**: Audio Encoder + Thinker, LM loss on transcript tokens. That's it. No conversation data, no response generation.
**Why simpler**: ASR is a well-defined supervised task. The Thinker sees audio features → predicts transcript. No need for dialogue pairs, synthetic conversations, or any of the earlier Phase 2 complexity.

### Job 2 — Qwen3-TTS-12Hz-1.7B: TTS fine-tune (voice)

**Goal**: synthesise natural Uzbek/Kazakh speech from text.
**Uzbek**: `uzlm/sayro-tts-1.7B` already exists — evaluate first, train only if quality is poor.
**Kazakh**: fine-tune `Qwen/Qwen3-TTS-12Hz-1.7B-Base` from scratch on IS2AI Kazakh data.

---

## Corrected VRAM math

**Qwen3-Omni-30B-A3B** (MoE — 30B total params, 3B active per token):

| Precision | VRAM | A6000 needed |
|---|---|---|
| bf16 (training) | ~60 GB | 2× (but tight — use QLoRA instead) |
| QLoRA: 4-bit Thinker + bf16 encoder + bf16 adapters | ~23 GB | 1× A6000 (comfortable) |
| Serving (Thinker 4-bit, encoder bf16) | ~19 GB | 1× A6000 |

Use **QLoRA** for training — quantize only the Thinker's expert weights to NF4; keep the audio encoder in bf16. The encoder is small (~1–2 GB) and is the part you're actively adapting for ASR. NF4 on it costs audio fidelity for almost no VRAM saving. Fits on 1× A6000 with room to spare.

**Qwen3-TTS-12Hz-1.7B**: ~3.4 GB bf16. Fits on any GPU.

**GPU assignment:**

```
GPUs 0–1: Qwen3-Omni ASR fine-tune (Uzbek)
GPUs 2–3: Qwen3-Omni ASR fine-tune (Kazakh, after Uzbek validated)
GPUs 4–5: Qwen3-TTS Kazakh fine-tune
GPUs 6–7: spare / eval / serving during training
```

---

## Day 0 — Two validation gates before any GPU time

These run before Day 1. They don't block starting, but they determine what you're building toward, so find out now rather than on Day 5.

### Gate A — Claude Uzbek/Kazakh text quality (CRITICAL PATH)

The entire Option-B product thesis is that the warmth that matters for Uzbek/Kazakh is lexical — register, formality, respectful address forms — and that lives in Claude's text output. If Claude's Uzbek/Kazakh is textbook-stiff instead of how people actually speak, a perfect Sayro voice makes it *worse*: the fluency mismatch is jarring in a way bad audio isn't.

Validate this before wiring anything:

```python
# Generate 20 representative assistant responses in each language
PROMPTS = [
    # Reminder confirmation
    "Ertaga soat 5da uyg'on eslatmasini o'rnat",
    # Briefing
    "Bugungi vazifalarimni aytib ber",
    # Code-switched query (Uzbek + Russian as people actually speak)
    "Privet, bugun meeting bor, eslatib tur",
    # Polite refusal
    "Bugun dam olish kunida ishlamayman de",
    # Warm acknowledgment
    "Rahmat, siz juda yaxshi yordam berdingiz",
    # Kazakh equivalents...
]

# Have Claude respond in Uzbek / Kazakh to each
# Then: native speaker naturalness score (1-5), register score (1-5)
# Target: avg ≥ 3.5/5 on both
```

**Decision gate:**
- If naturalness ≥ 3.5: proceed with Claude as brain. Done.
- If naturalness 2.5–3.5: add a post-processing prompt ("respond in natural spoken Uzbek, not written/formal — use everyday vocabulary and natural connectives"). Re-eval.
- If naturalness < 2.5: Claude's Uzbek/Kazakh is too weak for the product goal. This is a bigger change than anything else in the plan — you'd need a fine-tuned text model for the response layer. Stop and decide before Day 1.

### Gate B — ASR model choice: Omni vs. Whisper-large-v3

With the brain contract simplified, Omni's job is ASR only. That's a 30B MoE (17 GB at 4-bit) doing speech recognition. A fine-tuned Whisper-large-v3 would fit in ~3 GB and be simpler to serve. The question is code-switching.

**Also check first:** are Uzbek and Kazakh in Qwen3-Omni's speech-understanding language set? If not, the audio encoder is adapting from a weak phonological base — expect the fine-tune to need more data and more epochs than the table shows.

Quick bake-off (~4 hours, before committing to training):

```bash
# Step 1: Build a 200-sample code-switched eval set
# Include: pure Uzbek, pure Kazakh, Uzbek+Russian mixed, Kazakh+Russian mixed
# Sources: pull from your IS2AI held-out test split

# Step 2: Run zero-shot WER on both
python eval_wer.py \
  --model openai/whisper-large-v3 \
  --data eval/uz_kk_codeswitched.jsonl

python eval_wer.py \
  --model Qwen/Qwen3-Omni-30B-A3B-Instruct \
  --return-audio false \
  --data eval/uz_kk_codeswitched.jsonl
```

**Decision gate:**
- If Omni WER < Whisper by > 10 percentage points on code-switched: worth the 14 GB VRAM premium. Use Omni.
- If gap < 10 pp: use Whisper-large-v3. Fine-tune it instead (simpler, cheaper, faster to serve). Omni moves to "nice to have later."
- Either way the TTS leg and the brain contract are identical. Only the ASR model changes.

### Gate C — Kazakh TTS tokenizer check + smoke test (~1 hour)

Kazakh Cyrillic includes letters Russian doesn't: **ә, ғ, қ, ң, ө, ұ, ү, і**. The Qwen3-TTS tokenizer covers Russian, so most of Kazakh is in-vocab — but these 8 characters may fragment into unknown tokens. If they do, grapheme training won't converge well and you're on the G2P/phoneme path, which adds a real workstream (not "run another epoch").

Check this before Day 1 (takes ~10 minutes):

```python
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("Qwen/Qwen3-TTS-12Hz-1.7B-Base")

kazakh_chars = "ә ғ қ ң ө ұ ү і"
test_sentences = [
    "Қайырлы таң! Бүгін ауа-райы жақсы.",
    "Сізге ертең кездесу бар, ұмытпаңыз.",
    "Жарайды, мен сізге көмектесемін.",
]
for s in test_sentences:
    tokens = tok.tokenize(s)
    unk_count = tokens.count("[UNK]") + tokens.count("▁")
    print(f"Tokens: {len(tokens)}, UNK-like: {unk_count}")
    print(tokens)
```

**Decision gate:**
- If Kazakh chars tokenize cleanly (no `[UNK]`, fragmentation ≤ 2 tokens per word): proceed with grapheme training as planned.
- If heavy fragmentation: switch to G2P preprocessing — convert Kazakh text to IPA/phonemes before encoding. Budget 1-2 extra days (G2P model setup + regenerate training data). This is known to work for other languages where the tokenizer was thin.

Then run a 2-hour smoke test on Day 1 (5 utterances, single speaker) — see Day 1 plan.

---

## Do this today, before anything else

**1. Request Sayro access now** (manual review, takes days):
- Go to `huggingface.co/uzlm/sayro-tts-1.7B`
- Click "Access repository" and agree to their terms
- Check the license for commercial use — if research-only, you will fine-tune your own Uzbek TTS regardless of quality, so the Day-1 eval is moot

**2. Install the correct TTS package** (not `AutoModel`):
```bash
pip install git+https://github.com/QwenLM/Qwen3-TTS.git
# Sayro loads the same way — it's a Qwen3-TTS-12Hz fine-tune
```

**3. Add language detection + Claude language prompt** to `qwen_realtime.py`:
```python
# Detect transcript language and pass to engine as context
# Claude is already multilingual — just tell it which language to respond in
_LANG_PROMPT = {
    "uz": "Respond in Uzbek (O'zbekcha). Use natural, respectful Uzbek.",
    "kk": "Respond in Kazakh (Қазақша). Use natural, respectful Kazakh.",
}
```

---

## Data

### ASR data (Qwen3-Omni fine-tune)

**Uzbek:**
| Source | Hours | Quality note |
|---|---|---|
| IS2AI Uzbek ASR | ~1,000 h | Clean read speech, good for encoder |
| Mozilla Common Voice UZ | ~50 h | Diverse speakers, accents |
| javohirtoshqorgonov/uzbek-voice-dataset | ~20 h | |
| biruniy.uz audiobook | ~500 h | Clean but reading style |
| **Your linked datasets** | varies | Use all of them |

**Kazakh:**
| Source | Hours | Quality note |
|---|---|---|
| IS2AI Kazakh ASR (issai.nu.edu.kz) | ~1,200 h | Primary source |
| Mozilla Common Voice KK | ~50 h | Diverse speakers |

No synthetic audio needed for ASR. Real speech only.

Format (simple JSONL):
```json
{"audio": "data/uz/chunk_001.wav", "transcript": "Bugun havo juda yaxshi."}
{"audio": "data/kk/chunk_001.wav", "transcript": "Бүгін ауа-райы өте жақсы."}
```

### TTS data (Qwen3-TTS fine-tune — Kazakh only)

**Single speaker only.** The official `sft_12hz.py` is single-speaker / fixed-voice. Multi-speaker fine-tuning is listed as a future release. For a personal assistant this is the right call anyway — one consistent, recognisable voice is what you want.

**Pick your one speaker:** find the cleanest, most expressive natural Kazakh speaker in your IS2AI data. Aim for 5–10 hours of clean utterances from that speaker. Trim anything noisy, overlapping, or short < 1 second.

**Priority order**: real expressive native speech > read speech > synthetic. No multi-speaker diversity needed — depth from one speaker beats breadth from many.

Format for `prepare_data.py` (pre-tokenizes audio → codec tokens before training):
```json
{"audio_path": "data/kk/spk01/001.wav", "text": "Бүгін ауа-райы өте жақсы.", "ref_audio_path": "data/kk/spk01/ref.wav"}
{"audio_path": "data/kk/spk01/002.wav", "text": "Жарайды, мен сізге көмектесемін.", "ref_audio_path": "data/kk/spk01/ref.wav"}
```

Same `ref_audio_path` for every sample — speaker consistency requires a fixed reference. Include code-switching examples (Kazakh + Russian words) because that's how people actually speak.

---

## Day-by-day plan

### Day 1 — Data prep + Sayro eval + Kazakh TTS smoke test

```bash
# Prepare ASR data
python scripts/prepare_asr.py \
  --langs uz kk \
  --sources is2ai mozilla local \
  --min-duration 2 --max-duration 20 \
  --output data/asr

# Prepare Kazakh TTS data — single speaker, fixed ref_audio
# (multi-speaker is not supported by sft_12hz.py)
python scripts/prepare_tts.py \
  --lang kk \
  --speaker spk01 \          # pick one best speaker from IS2AI
  --ref-audio data/kk/spk01/ref.wav \
  --output data/tts/kk

# Pre-tokenize Kazakh TTS data to codec tokens (required before sft_12hz.py)
cd Qwen3-TTS/finetuning
python prepare_data.py \
  --data ../../data/tts/kk/train.jsonl \
  --model Qwen/Qwen3-TTS-12Hz-1.7B-Base \
  --output ../../data/tts/kk/tokenized

# Eval Sayro if access granted
from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel
model = Qwen3TTSModel.from_pretrained("uzlm/sayro-tts-1.7B")
# Generate 20 test sentences, listen to them
# If good: Uzbek TTS = done. If robotic: add to fine-tune queue on GPUs 4-5.

# Kazakh TTS smoke test — 5 utterances, single speaker, 2 hours on 1 GPU
# Reveals tokenizer fragmentation and adaptation problems BEFORE the overnight run
CUDA_VISIBLE_DEVICES=4 python Qwen3-TTS/finetuning/sft_12hz.py \
  --model Qwen/Qwen3-TTS-12Hz-1.7B-Base \
  --data data/tts/kk/tokenized/sample_5.jsonl \  # 5 samples only
  --lr 5e-5 \
  --steps 100 \
  --output checkpoints/kk/tts_smoke
# Then generate audio from the smoke checkpoint and listen.
# If the model produces recognisable Kazakh phonemes: full training is safe.
# If output is garbled: tokenizer fragmentation — see Gate C escalation path.
```

---

### Day 2–3 — Uzbek ASR fine-tune (GPUs 0–1) + Kazakh TTS fine-tune (GPUs 4–5)

**Uzbek ASR on GPUs 0–1 (QLoRA):**

```bash
CUDA_VISIBLE_DEVICES=0,1 torchrun --nproc_per_node=2 \
  train_asr.py \
  --model Qwen/Qwen3-Omni-30B-A3B-Instruct \
  --return-audio false \            # disable Talker — we don't use it
  --data data/asr/uz/train.jsonl \
  --lora-rank 64 \
  --lora-alpha 128 \
  --qlora true \                    # 4-bit Thinker experts only
  --qlora-exclude audio_encoder \   # keep encoder in bf16 — it's small and you're adapting it
  --target-modules audio_encoder,thinker \
  --loss lm \                       # next-token on transcript, no CTC
  --batch-size 4 \
  --grad-accum 8 \
  --lr 1e-4 \
  --epochs 3 \
  --output checkpoints/uz/asr \
  --save-steps 200
```

**Kazakh TTS full fine-tune on GPUs 4–5:**

```bash
cd Qwen3-TTS/finetuning

# Step 1: pre-tokenize (must run before sft_12hz.py)
python prepare_data.py \
  --data ../../data/tts/kk/train.jsonl \
  --model Qwen/Qwen3-TTS-12Hz-1.7B-Base \
  --output ../../data/tts/kk/tokenized

# Step 2: full fine-tune (sft_12hz.py optimizes all model parameters)
# 1.7B at bf16 = ~3.4 GB — full fine-tune is fine on A6000, no LoRA needed
CUDA_VISIBLE_DEVICES=4,5 torchrun --nproc_per_node=2 \
  sft_12hz.py \
  --model Qwen/Qwen3-TTS-12Hz-1.7B-Base \
  --data ../../data/tts/kk/tokenized \
  --lr 5e-5 \
  --epochs 3 \
  --save-steps 500 \
  --output ../../checkpoints/kk/tts
```

Note: `finetune.py` and `--lora-rank` flags don't exist on the official script. `sft_12hz.py` trains all parameters; that's fine at 1.7B. Multi-speaker support is a future release — the single-speaker fixed-ref_audio approach above is the supported path.

Expected duration: ~24–36 h for ASR, ~12–18 h for TTS (1.7B full fine-tune is fast). Runs overnight.

---

### Day 3 — Wire Uzbek end-to-end, validate before touching Kazakh

**Integration changes (minimal):**

1. **`nemo-voice/server/qwen_realtime.py`** — switch Omni to text-output mode:

```python
_VOICE_LANG = os.environ.get("VOICE_LANG", "en")
_EXTERNAL_TTS = _VOICE_LANG in ("uz", "kk")

def _session_config(voice: str) -> dict:
    modalities = ["text"] if _EXTERNAL_TTS else ["text", "audio"]
    # when text-only: Omni outputs transcript + response text, no audio tokens
    return {"type": "session.update", "session": {"modalities": modalities, ...}}
```

2. **`nemo-voice/server/tts_client.py`** — new file, wraps Qwen3-TTS via vLLM:

```python
"""Qwen3-TTS-12Hz client — streams audio from text for uz/kk."""
from __future__ import annotations
import aiohttp

class Qwen3TTSClient:
    def __init__(self, url: str) -> None:
        self._url = url  # http://localhost:8766

    async def stream_audio(self, text: str, lang: str):
        """POST to /v1/audio/speech (OpenAI-compatible), yield PCM chunks."""
        payload = {"model": f"qwen3-tts-{lang}", "input": text, "stream": True}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self._url}/v1/audio/speech", json=payload) as r:
                async for chunk in r.content.iter_chunked(4096):
                    yield chunk
```

3. **`nemo-voice/server/pump_class.py`** — intercept text delta, push to TTS client:

```python
# In _on_text_delta(): when _EXTERNAL_TTS, buffer text into clauses and stream TTS
async def _on_text_delta(self, text: str) -> None:
    if not _EXTERNAL_TTS:
        return
    for clause in self._clause_buf.feed(text):
        async for pcm in self._tts.stream_audio(clause, _VOICE_LANG):
            await self._send({"type": "audio", "data": b64(pcm)})
```

4. **`pyclaudir/engine/engine.py`** — add language hint to Claude system prompt:

```python
# Detect language from transcript, inject into system prompt
if voice_lang in ("uz", "kk"):
    system_addendum = _LANG_PROMPT[voice_lang]
    # prepend to every submit() call for this session
```

**Uzbek validation checklist** (do not start Kazakh until all pass):

- [ ] Uzbek WER < 15% on 100-sample held-out set
- [ ] TTS output sounds natural to a native speaker (5-minute listen test)
- [ ] End-to-end: speak Uzbek → get Uzbek audio response, latency measured
- [ ] Claude responds in Uzbek: "Eslatma: soat 5da onangizga qo'ng'iroq qiling"
- [ ] Code-switching handled: "Privet, qandaysiz?" → Uzbek response
- [ ] Tool delegation still works: reminder set, timer started, memory retrieved

---

### Day 4 — Kazakh ASR fine-tune (GPUs 0–1, warm-start from Uzbek)

```bash
CUDA_VISIBLE_DEVICES=0,1 torchrun --nproc_per_node=2 \
  train_asr.py \
  --model Qwen/Qwen3-Omni-30B-A3B-Instruct \
  --data data/asr/kk/train.jsonl \
  --qlora true \
  --lora-rank 64 \
  --load-from checkpoints/uz/asr/adapter_final \  # warm-start — both Turkic
  --epochs 2 \            # fewer epochs: warm-start converges faster
  --output checkpoints/kk/asr
```

Warm-start rationale: Uzbek and Kazakh share Turkic phonology and heavy Russian borrowings. The Uzbek adapter gives the Kazakh fine-tune a much better starting point than random init. Expect ~30% faster convergence — treat as a hypothesis, watch the loss curve.

---

### Day 5 — Merge, quantize, serve both languages

**Merge ASR adapters:**
```python
from peft import PeftModel
base = load_qwen3_omni("Qwen/Qwen3-Omni-30B-A3B-Instruct")
for lang, ckpt in [("uz", "checkpoints/uz/asr"), ("kk", "checkpoints/kk/asr")]:
    m = PeftModel.from_pretrained(base, ckpt).merge_and_unload()
    m.save_pretrained(f"models/qwen3-omni-{lang}-merged")
```

**Quantize for serving (optional — Thinker expert weights only, Audio Encoder stays bf16):**

AWQ calibration is text-only by default, which can dent an audio-conditioned model. If calibration gets fussy, skip quantization entirely and serve merged bf16 on 2× A6000 — you have the GPUs, footprint isn't a constraint. Don't let quantization block you.

```bash
# Option A: AWQ (Thinker only, encoder stays bf16) — ~19 GB, fits 1× A6000
python -m awq.entry \
  --model_path models/qwen3-omni-uz-merged \
  --quant_path models/qwen3-omni-uz-awq \
  --quant_config '{"modules_to_not_quantize": ["audio_encoder"]}' \
  --w_bit 4 --q_group_size 128

# Option B: serve bf16 directly — ~60 GB, needs 2× A6000 (tensor-parallel-size 2)
# Use this if AWQ calibration quality is degraded or the process fails
```

**Serve:**
```bash
# Omni (ASR + text out) — AWQ path
vllm serve models/qwen3-omni-uz-awq \
  --port 8765 --tensor-parallel-size 1

# Omni — bf16 fallback path
vllm serve models/qwen3-omni-uz-merged \
  --port 8765 --tensor-parallel-size 2 --dtype bfloat16

# TTS — Uzbek (Sayro or fine-tuned)
# Note: `python -m qwen_tts.server` is not a confirmed entry point.
# Use vLLM-Omni if the /v1/audio/speech endpoint is ready in your version,
# otherwise fall back to the community voice_clone_server.py (Flask):
python voice_clone_server.py \
  --model uzlm/sayro-tts-1.7B --port 8766

# TTS — Kazakh (same server, different model)
python voice_clone_server.py \
  --model models/qwen3-tts-kk --port 8767
```

Confirm which TTS serving path actually works before Day 5 — test it on Day 3 when you wire the Uzbek integration.

**.env additions:**
```bash
VOICE_LANG=uz             # or kk, or en (falls back to today's behaviour)
QWEN_REALTIME_URL=ws://localhost:8765
VOICE_TTS_URL=http://localhost:8766   # changes to 8767 for kk
```

---

### Day 6 — Kazakh end-to-end validation + both languages live

Same checklist as Uzbek. If Kazakh TTS needs another epoch, run it on GPUs 4-5 while GPUs 0-3 are idle. Known risk: Kazakh is morphologically more complex than Uzbek — WER target is 15%, but accept 20% on first pass if the phoneme quality is right and it improves with data.

---

## Model storage

**During training — S3 sync after each phase:**
```bash
aws s3 sync checkpoints/ s3://nemo-models/uzbek-kazakh/checkpoints/
```

**Final models — HuggingFace private repos:**
```bash
# LoRA adapters (~200 MB each) — lightweight, version-controlled
huggingface-cli upload khamidov17/qwen3-omni-uz-asr checkpoints/uz/asr/ --private
huggingface-cli upload khamidov17/qwen3-omni-kk-asr checkpoints/kk/asr/ --private
huggingface-cli upload khamidov17/qwen3-tts-kk checkpoints/kk/tts/ --private

# Merged + quantized Omni (~17 GB each)
huggingface-cli upload khamidov17/qwen3-omni-uz-awq models/qwen3-omni-uz-awq/ --private
huggingface-cli upload khamidov17/qwen3-omni-kk-awq models/qwen3-omni-kk-awq/ --private
```

**Pull on serving machine:**
```bash
huggingface-cli download khamidov17/qwen3-omni-uz-awq --local-dir /models/omni-uz
huggingface-cli download khamidov17/qwen3-tts-kk --local-dir /models/tts-kk
```

---

## Timeline

| Day | GPUs 0–1 | GPUs 2–3 | GPUs 4–5 | GPUs 6–7 |
|---|---|---|---|---|
| 0 | Gate A: Claude UZ/KK text eval (no GPU) | — | Gate B: Omni vs Whisper bake-off; Gate C: tokenizer check | — |
| 1 | Data prep + `prepare_data.py` | Data prep | Sayro eval + **Kazakh TTS smoke test** (5 samples) | — |
| 2–3 | Uzbek ASR fine-tune (QLoRA) | idle | Kazakh TTS full fine-tune (`sft_12hz.py`) | idle |
| 3 | **Wire + validate Uzbek E2E; confirm TTS serving path** | — | — | — |
| 4 | Kazakh ASR (warm-start from Uzbek) | idle | Kazakh TTS eval | idle |
| 5 | Merge + quantize (or serve bf16) | — | — | — |
| 6 | **Validate Kazakh E2E** | — | — | — |

---

## Expected results

| Metric | Before | After |
|---|---|---|
| Uzbek STT WER | ~40% | < 15% |
| Kazakh STT WER | ~50% | < 18% |
| TTFSW (chitchat) | 300–500 ms | ~350 ms |
| TTFSW (brain task) | Claude round-trip | same — Claude round-trip |
| Uzbek audio output | Broken / English | Natural Uzbek |
| Kazakh audio output | Broken / English | Natural Kazakh |
| Code-switching | Bad | Handled (real speech in training data) |
| Omni VRAM (serving) | 14 GB | 19 GB (AWQ, 1× A6000) or 60 GB bf16 (2× A6000) |
| TTS VRAM | 0 | 3.4 GB per language (1.7B bf16) |
| Brain | Claude (unchanged) | Claude (unchanged) |

---

## Escalation paths

**Sayro is research-only / not commercial:** fine-tune Uzbek TTS yourself on GPUs 4-5 alongside Kazakh (add 1-2 days, same pipeline).

**WER still bad after 3 epochs:** add a second pass on conversational/spontaneous speech specifically — read speech doesn't cover natural Uzbek/Kazakh pronunciation variation.

**Claude's Uzbek/Kazakh quality poor (flagged by Gate A):** first try a register-forcing system prompt ("respond in natural spoken Uzbek — everyday vocabulary, not formal/written register"). If that gets avg < 3.5/5 from natives, you need a fine-tuned text model for the response layer — this is the largest change in the plan and must be decided before Day 1, not discovered on Day 5.

**Gate B chose Whisper instead of Omni:** swap the ASR fine-tune to Whisper-large-v3. Everything else (TTS, integration, brain) stays identical. Whisper serving footprint: ~3 GB vs 19 GB — a significant ops win if code-switching performance is comparable.

**Kazakh TTS smoke test failed / output garbled (Gate C fragmentation):** switch TTS training from grapheme text to phoneme/IPA sequences. Add a G2P preprocessing step (a Kazakh G2P model or IPA transliteration). Budget 1-2 extra days. This is the known workaround that works for languages with thin tokenizer coverage.

**Kazakh TTS sounds robotic after 3 epochs:** run more epochs; the adaptation may need more steps for a language this far from the training distribution. If robotic after 6 epochs with clean data, G2P is the likely fix — the tokenizer fragmentation hurts convergence even if phonemes are in-vocab.

**AWQ calibration degrades audio quality:** skip it, serve merged bf16 on 2× A6000. You have 8 GPUs. Footprint is not a constraint here.

**Latency > 500 ms on chitchat:** profile — TTS first packet should be ~97 ms, Omni first text token ~100-150 ms. If total > 350 ms it's network or clause-buffering logic, not the models.

**Prosodic carry-through needed later:** add a 2-class audio-emotion classifier (stressed/calm) that injects an affect tag into the TTS prompt. Cheap, independent of Omni, recovers ~70% of affect-mirroring without Talker fine-tuning.
