"""Background web search for the voice agent — fast factual lookups.

The prompt has always told Nemo "call web_search to look something up", but the
tool was never implemented — so every lookup stranded the turn (the session sat
idle for 300s and the app muted the mic). This is the real tool.

DuckDuckGo's HTML endpoint: no API key, reachable from the server, and a real
browser User-Agent avoids the bot-challenge page (a bot-ish UA gets a captcha).
Class-tolerant regexes pull the top result titles + snippets. Runs in the
BACKGROUND (see qwen_realtime._BG_TOOLS) so Nemo says "on it" and keeps talking
while it runs, then reads the answer back.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
import urllib.request

LOG = logging.getLogger("nemo.web_search")

TOOL_NAMES = {"web_search"}

_URL = "https://html.duckduckgo.com/html/"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_MAX_RESULTS = 3

FUNCTIONS: list[dict] = [
    {
        "name": "web_search",
        "description": (
            "Look something up on the web — news, facts, prices, weather, sports, "
            "anything current or that you don't already know. Runs in the "
            "BACKGROUND: say you're on it and keep chatting; the answer comes back "
            "in a moment for you to read to Avazbek, briefly and naturally."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."}
            },
            "required": ["query"],
        },
    }
]

# Tolerant to extra classes / attribute order in DuckDuckGo's HTML.
_TITLE_RE = re.compile(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>', re.S)
_SNIP_RE = re.compile(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.S)


def _clean(s: str) -> str:
    """Strip tags + unescape entities to plain spoken-friendly text."""
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def dispatch(name: str, args: dict) -> str:
    """Run one web search and return a short JSON result. BLOCKING (urlopen) —
    call via asyncio.to_thread so it never stalls the voice event loop."""
    if name != "web_search":
        return json.dumps({"error": f"unknown search tool {name}"})
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "nothing to search for"})
    try:
        data = urllib.parse.urlencode({"q": query}).encode()
        req = urllib.request.Request(_URL, data=data, headers={"User-Agent": _UA})
        body = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 — never crash the turn
        LOG.warning("web_search failed: %s", exc)
        return json.dumps({"error": "the search didn't go through"})
    # Pair each title with the snippet that FOLLOWS it (up to the next title) —
    # positional zip mis-pairs when a result (ad/special block) has a title but
    # no snippet, making Nemo read a title with the wrong description.
    titles = list(_TITLE_RE.finditer(body))
    out: list[str] = []
    for i, m in enumerate(titles):
        t = _clean(m.group(1))
        if not t:
            continue
        end = titles[i + 1].start() if i + 1 < len(titles) else len(body)
        snip = _SNIP_RE.search(body, m.end(), end)
        s = _clean(snip.group(1)) if snip else ""
        out.append(f"{t} — {s}" if s else t)
        if len(out) >= _MAX_RESULTS:
            break
    if not out:
        return json.dumps({"result": f"couldn't find anything useful for '{query}'"})
    return json.dumps({"result": " || ".join(out)})
