---
name: research-brief
description: General-purpose deep research playbook — any "find out / compare / is it true / what's the best X" request. Multi-source sweep, verify before asserting, deliver a decision-ready brief with sources. The default for open questions that aren't news digests (that's trends).
license: MIT
compatibility: Requires WebSearch, WebFetch, send_message.
---

# Skill: research-brief

## Method

1. **Decompose** — restate the question as 2–4 sub-questions; note what a
   "good enough to act on" answer looks like.
2. **Sweep** — search each sub-question separately; read at least 3
   independent sources for anything load-bearing (a price, a spec, a claim).
   Prefer primary sources (official docs, filings, the actual paper) over
   blog summaries.
3. **Verify** — for the 1–2 facts the conclusion hinges on, actively look for
   a source that contradicts them. Say so if sources disagree.
4. **Conclude** — a recommendation, not a survey. "Buy X because…" beats
   "here are five options."

## Comparisons ("which X should I…")

Build a criteria table (price, the 2–3 things he actually cares about from
the task text), fill it from live sources, then pick one and defend it in two
sentences. The table goes to Telegram; the pick goes in the voice line.

## Output

- Voice line: the conclusion + the single strongest reason.
- Telegram: the brief — conclusion first, then evidence per sub-question,
  then source links. Under a page unless he asked for depth.
- Every number/date/price carries its source inline.

## Voice line examples

- "Short answer: yes, the M4 Air handles it fine — the one caveat and full
  comparison are in your Telegram."
- "Best option is the Wise business account, mainly for the UZS conversion
  fees — breakdown's in Telegram."
