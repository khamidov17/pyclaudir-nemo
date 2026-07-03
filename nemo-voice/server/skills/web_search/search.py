#!/usr/bin/env python3
"""Concise web search for the voice agent — stdlib only, no API key.

Tries DuckDuckGo's Instant Answer API first (clean factual abstract), then
falls back to scraping the HTML results page for the top snippets. Returns a
short text answer suitable for reading aloud. Best-effort: prints a friendly
"couldn't find" line rather than erroring, so the voice turn always completes.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
import urllib.request

# A realistic browser UA — DDG serves a stripped challenge page (no results) to
# bot-looking agents, so a plain "compatible; X" string returns nothing.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_MAX = 700


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8", "ignore")


def _instant(query: str) -> str:
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
    )
    try:
        data = json.loads(_fetch(url))
    except Exception:
        return ""
    if data.get("AbstractText"):
        return data["AbstractText"]
    if data.get("Answer"):
        return str(data["Answer"])
    for topic in data.get("RelatedTopics") or []:
        if isinstance(topic, dict) and topic.get("Text"):
            return topic["Text"]
    return ""


def _html_results(query: str) -> str:
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    try:
        page = _fetch(url)
    except Exception:
        return ""
    # DDG uses multiple classes (e.g. "result__snippet js-result-snippet"), so
    # match result__snippet anywhere in the class list, not as the whole value.
    snippets = re.findall(
        r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', page, re.S
    )
    out: list[str] = []
    for raw in snippets[:3]:
        text = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        if text:
            out.append(text)
    return " ".join(out)


def main() -> None:
    query = " ".join(sys.argv[1:]).strip()
    if not query:
        print("No search query given.")
        return
    answer = _instant(query) or _html_results(query)
    print((answer or "I couldn't find anything reliable on that.")[:_MAX])


if __name__ == "__main__":
    main()
