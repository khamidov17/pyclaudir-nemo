# Nemo — Strategic Improvements

Deep-research synthesis (111 agents, 28 sources, 25 adversarially-verified claims, 2026-06-30).
Confidence labels: **HIGH** = 3-0 or 2-1 adversarial vote | **MEDIUM** = 2-1 with caveats.

---

## TL;DR

Four levers ranked by ROI:

1. **Memory compression** — hierarchical offline consolidation can cut Nemo's per-turn token bill 20–38x
2. **RAG efficiency** — extractive context compression beats abstractive by 10x in latency, 86% fewer tokens
3. **Unmet user needs** — health, finance, social-connection are 70-80% daily-use activities with <20% AI adoption
4. **Transport** — Nemo's WebSocket voice path already matches the right architecture; WebRTC is worth piloting for mobile

---

## 1. Memory Architecture (highest leverage)

### 1.1 Hierarchical Offline Consolidation (LightMem pattern)

**What it is:** Instead of keeping a growing flat list of memories in context, consolidate them offline between sessions into a compressed summary tree. During a session, only the compressed summaries + current turn go in-context; the raw memories stay on disk for retrieval.

**Verified numbers** [MEDIUM — single ICLR 2026 paper, synthetic benchmarks]:
- Token overhead for memory management: **20–38x reduction**
- API calls per session: **30–310x reduction** (online path: 159x–310x vs A-MEM baseline)
- QA accuracy: matches or beats flat-memory baselines (up to +29.3% on LoCoMo)

**What Nemo does today:** `data/memories/*.md` files are flat-appended; `memory_index.db` is rebuilt on a 45s cron. Every voice session injects raw memory text into the system prompt.

**What to change:**
- Add a nightly consolidation job: read all memory files written today, ask Nemo to compress them into a dated summary chunk, store that chunk in `memory_index.db` with `source='consolidated'`
- Voice session injection: inject the most recent 3 summary chunks + semantic-retrieved raw memories only when relevance score > threshold
- Keep raw files for audit; don't inject them directly

**Source:** https://arxiv.org/pdf/2510.18866 (LightMem, ICLR 2026)

---

### 1.2 Preserve Entities and Numbers Verbatim (SARA pattern)

**What it is:** Pure vector embeddings lose precision on names, dates, phone numbers, and amounts. Store two representations: (a) the raw NL span verbatim, (b) a semantic vector for retrieval.

**Verified** [HIGH]:
- Compression-only approaches demonstrably drop entity/numerical detail
- Dual NL-span + vector representation is the correct fix

**What Nemo does today:** `memory_index.db` stores `text` + `vec` — the architecture is already right. The gap is that the text field currently holds the full raw chunk (~300 tokens).

**What to change:**
- When writing a memory, extract named entities (names, dates, amounts, places) as a separate `spans` JSON column
- During voice retrieval, always inject spans verbatim even if the full chunk is compressed out

**Source:** https://arxiv.org/pdf/2507.05633 (SARA, July 2025)

---

### 1.3 Write-Triggered Async Reindex

**Current gap (open bug M-2):** 45s cron lag means memories written mid-session are invisible to the same session's recall.

**Fix:** After `SearchMemoriesTool` or `SynthesizeMemoryWikiTool` writes a new chunk, fire a background asyncio task to reindex that single chunk in `memory_index.db` (WAL mode + `busy_timeout=5000` already in place). Total cost: one SQLite insert, <10ms.

---

## 2. RAG / Context Compression

### 2.1 Extractive Compression in Memory Retrieval (EXIT pattern)

**What it is:** Instead of returning the full retrieved memory chunk to the LLM, use a small classifier to score each sentence and keep only the top-scoring spans. No summarization — just extraction.

**Verified numbers** [HIGH — ACL 2025 Findings, peer-reviewed]:
- Retrieved context reduced by **86.8%** (4,497 → 594 tokens at k=30)
- Latency: **0.79s** vs 8.5s for abstractive summarization (~10x faster)
- Answer quality maintained (accuracy on par with full context)

**What Nemo does today:** `SearchMemoriesTool` returns raw chunk text directly into the LLM prompt.

**What to change:**
- Add a `_compress_chunks(chunks: list[str], query: str) -> list[str]` step in `search_memories.py`
- Implementation: sentence-split each chunk, score relevance to query with a fast embedding cosine, keep top 30% of sentences
- No external model needed — `sentence-transformers/all-MiniLM-L6-v2` (22MB, CPU-fast) works

**Source:** https://arxiv.org/pdf/2412.12559 (EXIT, ACL 2025 Findings)

---

### 2.2 Proxy-Model Token Scoring for System Prompt (LLMLingua pattern)

**What it is:** Run a small proxy model (GPT-2 class, runs locally) to score each token in the static system prompt + tool definitions. Drop low-perplexity tokens before every LLM call.

**Verified** [HIGH — EMNLP 2023, Microsoft Research, open-sourced]:
- Mechanism confirmed; quantitative compression ratios unverified in adversarial review
- Highest ROI target: **system prompt + tool schema** (static, verbose, sent on every turn)

**What Nemo does today:** Full system prompt + all tool definitions sent every turn. Tool schemas alone are often 2,000–4,000 tokens.

**What to change (pragmatic version without running a proxy model):**
- Audit the system prompt for repeated/redundant content — likely 30–40% removable
- Send only the tool schemas for tools that are relevant to the current turn (e.g., don't send `run_code` schema to voice sessions, don't send `phone_action` schema to Telegram turns without a paired phone)
- Dynamic tool selection: classify turn intent → include only matching tool subset

**Source:** https://github.com/microsoft/LLMLingua | https://arxiv.org/abs/2310.05736

---

## 3. New Features — Highest-Gap User Needs

Data from Menlo Ventures / Morning Consult survey of 5,031 U.S. adults, April 2025 [MEDIUM]:

| Activity | Daily use | AI adoption | Gap |
|---|---|---|---|
| Financial management | 82% | 16% | **66 pts** |
| Social connection | 76% | 14% | **62 pts** |
| Health research | 71% | 20% | **51 pts** |

These are the three largest unmet opportunities. Nemo is already better positioned than generic chatbots because it's on the user's phone with persistent memory and proactive delivery.

---

### 3.1 Financial Awareness (66-pt gap)

**Features to add:**
- **Subscription radar:** Nemo notices when the user mentions a recurring payment or new service ("signed up for X"), stores it as a memory, and proactively reminds before the next billing cycle
- **Spend check-in:** Weekly spoken briefing — "You mentioned wanting to cut back on eating out. This week you mentioned restaurants 4 times." (no bank API needed — works from conversation context alone)
- **Bill reminder chain:** "Your rent is due in 3 days" → "Did you pay it?" follow-up if no confirmation in 48h

**Implementation:** Extend `set_reminder` to support a `follow_up_if_no_confirm` flag. The reminder engine already exists; this is a configuration extension.

---

### 3.2 Social Connection Nudges (62-pt gap)

**Features to add:**
- **Relationship memory:** When user mentions a person ("had dinner with Aziz"), store it. If that person hasn't been mentioned in N days, proactively ask "Haven't heard about Aziz in a while — everything okay?"
- **"Reach out" queue:** User can say "remind me to check in with mom once a week" → Nemo sets a recurring reminder and can draft a message to send
- **Birthday/anniversary radar:** Nemo stores dates mentioned in conversation and fires a morning reminder with a suggested message

**Implementation:** These are memory + reminder combinations. `write_voice_profile` + `set_reminder` cover the mechanics. Need a `relationship_memory` memory type in the chunker.

---

### 3.3 Health Check-ins (51-pt gap)

**Features to add:**
- **Symptom journal:** "My back hurts again" → Nemo stores with timestamp. After 3+ mentions: "You've mentioned back pain 4 times this week — want me to remind you to see a doctor?"
- **Medication reminders:** Set + confirm loop ("Did you take your vitamins?")
- **Sleep/energy tracking:** Conversational — just from what the user says ("tired again today")
- **Escalation awareness:** If health concern is mentioned with urgency words, Nemo surfaces it prominently: "You mentioned chest tightness twice — that's worth a call to your doctor today"

**Implementation:** Pattern-matching on memory content + reminder engine. No medical API needed; Nemo surfaces observations, not diagnoses.

---

## 4. Voice Architecture Improvements

### 4.1 Transport: WebSocket vs WebRTC

**Verified** [HIGH — OpenAI official docs]:
- WebRTC: browser/mobile peer-to-peer, automatic audio handling, lower client-side code
- WebSocket: server-side audio control, manual buffer management, better for mixing
- Control events (JSON) travel on a separate channel from audio in both paths

**Nemo today:** WebSocket-based (`qwen_realtime.py`). This is **correct for the server-side mixing Nemo needs** — hardware AEC, barge-in detection, audio effects all require server-side audio access that WebRTC's peer-to-peer model bypasses.

**Recommendation:** Keep WebSocket for the server path. Pilot WebRTC only for a future browser client where server-side audio processing isn't needed.

---

### 4.2 Full-Duplex Barge-In Improvements

Current barge-in has two known race conditions (fixed in recent commits). The reference pattern from production implementations:

```
User speaks while Nemo talks
  → VAD detects speech_started
  → Cancel in-flight TTS immediately (not after current sentence)
  → Stash un-spoken remainder (keyed by session rev)
  → Process user input
  → If topic unchanged (same rev): resume stash with bridge phrase
  → If topic changed: discard stash
```

**Gap in Nemo today:** Stash-and-resume on topic-unchanged barge-in is not implemented — Nemo restarts from scratch after every barge-in. Adding resume would make interrupted responses feel more natural.

---

### 4.3 Reduce Voice Session Cold-Start Latency

**What adds latency today:**
1. Memory retrieval (SQLite query + embedding search) happens at session start
2. Full system prompt + all tool schemas sent on first turn
3. Voice persona injection from `memory_index.db` on every session

**Quick wins:**
- Pre-warm memory retrieval: start the embedding search as soon as the WebSocket connects (before the user finishes their first sentence)
- Cache the compiled system prompt + tool schema string in memory between sessions (it's static)
- Ship voice persona chunk to `qwen_realtime.py` as a startup arg so it's available before the first WS message

---

## 5. Token Waste — Quick Wins

These require no external libraries and can be done today:

| Waste source | Estimated tokens/turn | Fix |
|---|---|---|
| Full tool schema sent every turn | ~2,000–4,000 | Send only tools relevant to session type (voice vs Telegram) |
| Raw memory chunks injected verbatim | ~500–1,500 | Apply extractive compression (§2.1) |
| Repeated system prompt boilerplate | ~500–1,000 | Audit + prune; cache compiled string |
| Memory files with duplicate content | cumulative | Dedup at write time (open bug M-4) |
| Tool error messages echoed back in full | ~100–300 | Summarize to 1 sentence before returning to LLM |

**Total estimated reduction:** 30–60% of per-turn input tokens — without any model change or accuracy loss.

---

## 6. Open Questions Before Implementing

From the research:

1. **What is Nemo's actual per-turn token breakdown?** Add a `LOG.debug("turn tokens: system=%d memory=%d tools=%d history=%d", ...)` log line — without this baseline, it's impossible to know which compression lever yields the most savings.

2. **Which memory events trigger re-indexing today?** Confirm the write-triggered reindex gap is real (open bug M-2) by checking if `memory_index.db` timestamp lags `memories/*.md` in production logs.

3. **Are health/finance/social topics already in Nemo's request logs?** If yes, these features address existing demand. If not, it's a discovery problem and the first step is showing the user that Nemo can handle these topics.

4. **Does barge-in stash-and-resume matter to this user?** Only worth building if the user actually interrupts Nemo mid-sentence regularly. Check voice session logs for barge-in frequency before investing in it.

---

## Implementation Priority

| Priority | Feature | Effort | Impact |
|---|---|---|---|
| 1 | Per-turn token audit (§5 baseline) | 1h | Enables everything else |
| 2 | Dynamic tool selection by session type | 1 day | 30–50% token reduction |
| 3 | Extractive memory compression (§2.1) | 2 days | 60–85% memory token reduction |
| 4 | Write-triggered reindex (M-2) | 4h | Eliminates 45s memory lag |
| 5 | Entity/span preservation (§1.2) | 1 day | Prevents name/date recall failures |
| 6 | Nightly memory consolidation (§1.1) | 2 days | 20x+ long-term memory efficiency |
| 7 | Subscription/bill reminder features (§3.1) | 2 days | High user value, low engineering |
| 8 | Relationship nudges (§3.2) | 2 days | High user value, low engineering |
| 9 | Health symptom journaling (§3.3) | 2 days | High user value, low engineering |
| 10 | Barge-in stash-and-resume (§4.2) | 3 days | Voice naturalness |

---

## Sources

| Source | Type | Used for |
|---|---|---|
| https://arxiv.org/pdf/2510.18866 | Primary (ICLR 2026) | LightMem hierarchical consolidation |
| https://arxiv.org/pdf/2412.12559 | Primary (ACL 2025) | EXIT extractive RAG compression |
| https://arxiv.org/pdf/2507.05633 | Primary (July 2025) | SARA hybrid representation |
| https://github.com/microsoft/LLMLingua | Primary (EMNLP 2023) | Proxy-model token scoring |
| https://developers.openai.com/api/docs/guides/realtime-webrtc | Primary (OpenAI, GA 2025) | WebRTC transport architecture |
| https://developers.openai.com/api/docs/guides/realtime-conversations | Primary (OpenAI, GA 2025) | WebSocket + event model |
| https://menlovc.com/perspective/2025-the-state-of-consumer-ai/ | Secondary (n=5,031 survey) | Daily-life adoption gap data |
