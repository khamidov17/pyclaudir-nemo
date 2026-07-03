# Nemo vs TML-Interaction-Small — Architecture Deep Dive

## What TML-Interaction-Small actually is

TML built a **276B MoE model (12B active parameters)** that is natively audio-in,
audio-out — no STT pipeline, no TTS pipeline, no VAD component. It processes
**200ms continuous interleaved streams** (no turn boundaries at all). Think of it
as: the model IS the voice stack — one neural net listening and speaking
simultaneously, with a separate background reasoning model running async.

Key metrics published:
- Turn-taking latency: **0.40s** (humans: ~0.2s)
- FD-bench v1.5 score: **77.8/100**
- Harmbench refusal: **99.0%**
- TimeSpeak (proactive initiation): **64.7%**

Source: https://thinkingmachines.ai/blog/interaction-models/

---

## Comparison Table

| Dimension | TML-Interaction-Small | Nemo TODAY (post voice-v2) | What it takes to match/exceed |
|---|---|---|---|
| **Voice I/O architecture** | Native audio↔model (dMel input, flow-head output). Zero STT/TTS pipeline | Qwen Omni Realtime = native audio I/O ✅ same concept, different model | Nothing — Qwen Omni already does this |
| **Turn detection** | None — 200ms interleaved streams, model decides when to speak | Server VAD (threshold-based silence detection) | M4: fine-tune on turn-taking signals; or tighten VAD params |
| **Full-duplex barge-in** | Native — model listens while speaking simultaneously | P4 scaffold built (kFullDuplex flag, AEC wired) but NOT battle-tested | Device validation + AEC tuning; optionally fine-tune Qwen on barge-in |
| **Turn-taking latency** | **0.40s** (best published) | Unmeasured — estimated 0.8–2s on heavy turns | Measure with FD-bench equivalent; M2 thinker prefetch closes most of gap |
| **Background reasoning** | Separate async background model for tool use / deep thinking | Tier-2 Claude Code engine + weave-in streaming ✅ (built) | Wire Tier-1 fast reasoner (Haiku) as the M2 thinker |
| **Streaming answer** | Background model streams into ongoing speech | Built: VOICE_STREAM_BRAIN + VOICE_WEAVE_IN ✅ | Enable flags + device validation |
| **Think-while-listening (M2)** | Model internally pre-processes during user speech | Not built — StateSnapshot exists but nothing fills it during speech | Async Haiku call on live transcript, populates StateSnapshot |
| **Proactive speech (TimeSpeak)** | Model initiates speech at right moment (64.7%) | Not implemented | Research-grade; needs fine-tuning + timing classifier |
| **Video input** | Continuous 40x40 hMLP patches, real-time stream | Discrete "look" tool (snapshot on demand) | Continuous video stream to Qwen Omni + streaming frame injection |
| **Languages** | English primarily, live translation mentioned | **English only** | Fine-tune Qwen3-Omni on UZB/RU/EN/KZ — see language section |
| **Semantic routing** | Internal to model (trained behavior) | semantic_router.py — rules-based TIER0/TIER2 ✅ | Add ML classifier for TIER1 routing accuracy |
| **Long-term memory** | Not mentioned / implicit in context | **Explicit semantic memory system** 🏆 structural advantage | Keep + extend |
| **Tool use** | Background model handles tools async | Full suite: code, web, calendar, phone, camera, meetings ✅ | Keep expanding |
| **Evaluation framework** | FD-bench v1/v1.5/v3, TimeSpeak, CueSpeak | None | Build FD-bench equivalent; measure turn-taking latency |
| **Self-hosted model** | Their own trained model (276B MoE) | Qwen API now; QWEN_REALTIME_URL ready for self-hosted | Deploy Qwen3-Omni via vLLM on owned GPU; point env var |
| **Fine-tuning** | End-to-end co-trained from scratch | None | QLoRA fine-tune Qwen3-Omni on UZB/RU/KZ speech + your persona |
| **Speculative answer (M4)** | Not described (different arch) | Not built | Tier-0 starts off plan; Tier-2 corrects mid-speech |
| **Safety/refusal** | 99.0% Harmbench | HMAC auth, sensitive_next flag, no public surface | Good for private system; add content filter if opening up |
| **Phone integration** | None described | Full Android: wakeword, AEC, action bridge, biometrics ✅ 🏆 | Keep — major differentiator |
| **Compute owned** | Yes (frontier lab) | AlphaVPS + separate GPU box | See GPU section |

---

## Language Plan: UZB / RU / EN / KZ

**Current reality:**
- Qwen Omni: English, Chinese, Japanese, Korean — strong
- Russian: decent Qwen coverage out of the box
- Uzbek: low-resource Turkic language — Qwen will hallucinate/struggle
- Kazakh: low-resource Turkic language — same problem
- **Key insight:** Uzbek and Kazakh are both Turkic — shared phoneme structures,
  script logic, grammar. A joint UZB+KZ fine-tune is more efficient than two
  separate ones.

### Phase 1 — Enable now (no fine-tuning)
- Set Qwen session language to auto-detect (it already handles RU/EN)
- Test: speak to Nemo in Russian → responses in Russian
- Uzbek/Kazakh will be shaky — it'll try but make phonetic and grammar errors
- Engineering: pass `input_audio_transcription` model that supports multilingual;
  set no `language` constraint so Qwen auto-detects per turn

### Phase 2 — Fine-tune Qwen3-Omni (needs GPU)

| Step | What | Dataset | Effort |
|---|---|---|---|
| Collect speech data | 10–50h UZB + KZ audio with transcripts | Common Voice UZB, Mozilla UZB, custom recordings | Medium |
| QLoRA fine-tune | Train on UZB/KZ/RU speech recognition + generation | Speech pairs + conversational data | 1–2 weeks on 2× A100 |
| Persona + dialect tuning | Make Nemo sound natural in each language | 200–500 curated example turns per language | Low |
| Evaluation | WER per language, naturalness rating | Human eval | Ongoing |

---

## GPU Roadmap (self-hosted)

| Phase | What to run | GPU spec | Why |
|---|---|---|---|
| **Now** | Inference: Qwen3-Omni (32B) via vLLM | 2× A100 80GB or 4× A40 48GB | Full model fits in 160GB; real-time audio needs low-latency VRAM |
| **Fine-tune** | QLoRA Qwen3-Omni on UZB/RU/KZ | 2–4× A100 80GB | QLoRA reduces VRAM; 32B needs ~80GB+ even quantized during training |
| **M2+M4 concurrent** | Thinker + Qwen simultaneously | 4× A100 or H100s | Two models running in parallel for real-time |
| **Full TML-parity** | Train own MoE interaction model | 8–16× H100 cluster | Frontier-lab territory; do QLoRA on Qwen3-Omni first |

---

## Build priority (full roadmap)

| Priority | Work | Milestone | Flag | Est. effort |
|---|---|---|---|---|
| 1 | **Self-host Qwen3-Omni** via vLLM, point QWEN_REALTIME_URL | No API key, lower latency, full control | env var | 1–2 days |
| 2 | **Enable + measure** VOICE_STREAM_BRAIN + VOICE_WEAVE_IN on device | Confirm weave-in end-to-end | flags on | 1 day |
| 3 | **M2 Thinker**: async Haiku call during speech → StateSnapshot | Think-while-listening | VOICE_THINKER | 3–5 days |
| 4 | **FD-bench equivalent**: measure turn-taking latency per turn type | Know your baseline | metric infra | 2 days |
| 5 | **RU + UZB language test**: Qwen out-of-box quality, document gaps | Language baseline | session lang param | 1 day |
| 6 | **Tier-1 routing**: wire Haiku path for "real but not heavy" questions | M3 | VOICE_TIERS | 3 days |
| 7 | **Fine-tune Qwen3-Omni on UZB/KZ/RU** | Language quality | QLoRA job | 2–3 weeks + GPU |
| 8 | **Continuous video stream** (replace discrete "look" tool) | M4 precursor | VOICE_VIDEO_STREAM | 1 week |
| 9 | **M4 speculative hand-off** | TML-parity feel | VOICE_SPECULATIVE | research, 2–4 weeks |
| 10 | **Proactive initiation** (TimeSpeak equivalent) | Beyond TML | VOICE_PROACTIVE | research |

---

## Where Nemo already beats TML

1. **Explicit long-term memory** — TML has no persistent memory system. Nemo has
   semantic memory + fact extraction + profile synthesis. This grows over time —
   TML resets each session.
2. **Phone integration** — action bridge, wakeword, biometrics, notifications,
   accessibility service. TML is a cloud API with no device layer.
3. **Tool breadth** — web search, code execution, calendar, meetings recorder,
   Telegram, GitHub, reminders. TML has generic async tool use.
4. **Privacy model** — sensitive_next flag, on-device wakeword, HMAC-gated
   internals, no data leaves your stack. TML is a SaaS product.
5. **Fully ownable** — 100% of Nemo can run on hardware you own (Qwen3-Omni
   open weights + Claude self-serve or open reasoner). TML is proprietary.
6. **Multilingual target** — English + UZB + RU + KZ in the roadmap; TML is
   English-first.
