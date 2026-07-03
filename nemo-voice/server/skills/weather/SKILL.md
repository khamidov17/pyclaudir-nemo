---
name: get_weather
description: Get the current weather for a city or place. Use when Avazbek asks about the weather, what to wear, or whether to take an umbrella. Pass the place name (e.g. "Tashkent", "Beijing"); default to where he is if he doesn't say.
params: ["location"]
argv: ["curl", "-s", "--max-time", "12", "wttr.in/{location}?format=3&m"]
timeout: 15
---

# Weather skill

Returns a one-line current-weather summary from **wttr.in** — free, no API key,
reachable from the server. Example output: `Tashkent: ⛅️ +18°C`.

This file IS the skill: the frontmatter above defines the tool the voice agent
sees. To add another skill, drop a new `skills/<name>/SKILL.md` with its own
`name`, `description`, `params`, and `argv` — no code change, just restart.
