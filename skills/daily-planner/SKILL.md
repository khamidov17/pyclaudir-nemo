---
name: daily-planner
description: Everyday-life playbook — trip and itinerary planning, appointment and errand scheduling, shopping research and price checks, gift ideas, event planning. Turns vague intents into a concrete plan with times, places, and prices from live sources.
license: MIT
compatibility: Requires WebSearch, WebFetch, send_message.
---

# Skill: daily-planner

## Trips

Itinerary as day blocks (morning/afternoon/evening), each with place, cost
estimate, and transit between stops. Check live: opening days/hours, visa
rules if international, weather for the dates. Budget the whole trip with a
total. Flag the one booking that sells out first — book that today.

## Scheduling / errands

Batch by geography and opening hours (pharmacy + bank on the same side of
town, in the window both are open). Output a timed checklist; offer to set a
reminder for each fixed-time item via the engine.

## Shopping

For anything over ~trivial cost: 3 options (cheap / recommended / premium),
live prices with store links, and the recommended one defended in a sentence.
Uzbekistan-local: check uzum.uz / asaxiy.uz alongside global stores; note
customs on imports.

## Gifts

Ask-free method: infer the person from the task text (who, occasion,
budget), give 3 ideas ranked, each with a one-line "why them". Include one
non-obvious option.

## Events (dinners, gatherings)

Venue shortlist (3, with price band and booking method), timing, and a
what-to-confirm checklist. If a booking needs a phone call, say so — don't
pretend to book.

## Output

Plans/tables/links → Telegram. Voice line = the plan's shape + the next
action: "Weekend in Samarkand is planned — trains at 8am both ways, about
$140 total; book the Registan guide first, rest is in Telegram."
