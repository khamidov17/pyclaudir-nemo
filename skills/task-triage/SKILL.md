---
name: task-triage
description: Router for delegated voice tasks. Read FIRST whenever a task arrives from Nemo's voice channel (delegate_task). Maps the request to the right playbook skill, sets the response contract (short spoken summary back to voice, full artifact to Telegram), and defines how to report completion proactively.
license: MIT
compatibility: Requires list_skills, read_skill, send_message.
---

# Skill: task-triage

A task delegated from the voice channel means Avazbek asked Nemo something
mid-conversation and Nemo handed it to you while continuing to talk with him.
Your job: pick the right playbook, do the work, and hand back something a
voice can SAY.

## Route

Match the task to a playbook and `read_skill` it before working:

| Task smells like | Read |
|---|---|
| studying, lectures, exams, homework, explain a concept, flashcards | `study-assistant` |
| "find out / research / compare / what's the state of X", any cited deep-dive | `research-brief` |
| write/draft anything — email, essay, report, post, proposal, CV | `writing-desk` |
| meetings, competitors, pricing, invoices, clients, market moves | `business-ops` |
| trips, bookings, appointments, shopping, errands, gifts, schedules | `daily-planner` |
| global/Uzbek news digest | `trends` / `trends-uzbekistan` |

Multi-part tasks: read every matching playbook, work them in parallel where
independent, one combined report at the end.

## Response contract (always)

1. **Voice line** — your task result MUST start with 1–3 sentences written to
   be SPOKEN: no markdown, no lists, no URLs, conversational, lead with the
   answer. Nemo reads this aloud proactively the moment you finish.
2. **Full artifact** — anything longer (the essay, the table, the itinerary,
   links) goes via `send_message` to Telegram, and the voice line mentions it:
   "…full version is in your Telegram."
3. **Blocked?** If you genuinely need a decision from Avazbek, return ONE
   spoken question, not a menu.

## Rules

- Never re-ask what the voice already told you; the task text is the brief.
- Time-sensitive facts (prices, schedules, news) must come from live search,
  not memory.
- He speaks Uzbek/English/Russian mixed — write the voice line in the
  language of the task text.
