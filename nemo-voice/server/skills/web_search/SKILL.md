---
name: web_search
description: Search the web for current information, facts, news, prices, or anything you don't already know. Use it IMMEDIATELY and silently the moment Avazbek asks you to look something up, search, or asks about current events / facts you're unsure of — then read the answer straight back to him. Never say "give me a minute" and go quiet; just search and answer.
params: ["query"]
argv: ["python3", "{skill_dir}/search.py", "{query}"]
timeout: 14
---

# Web search skill

A self-contained search: a small stdlib-only Python helper next to this file
queries DuckDuckGo (no API key, reachable from the server) and returns a short
text answer for Nemo to read aloud. `{skill_dir}` resolves to this folder so the
helper is found regardless of the process working directory.
