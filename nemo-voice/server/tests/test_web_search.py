"""web_search — parses DuckDuckGo HTML into short spoken results. Network is
mocked so the test is hermetic."""

from __future__ import annotations

import io
import json

import web_search

_FAKE_HTML = """
<div class="result">
  <a class="result__a" href="x">First Title</a>
  <a class="result__snippet" href="x">First snippet text.</a>
</div>
<div class="result">
  <a class="result__a result__a--big" href="y">Second &amp; Title</a>
  <a class="result__snippet" href="y">Second <b>snippet</b>.</a>
</div>
"""


def _patch(monkeypatch, body: str):
    monkeypatch.setattr(
        web_search.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(body.encode()),
    )


def test_parses_titles_and_snippets(monkeypatch):
    _patch(monkeypatch, _FAKE_HTML)
    out = json.loads(web_search.dispatch("web_search", {"query": "x"}))
    assert "First Title — First snippet text." in out["result"]
    # entities unescaped, inner tags stripped
    assert "Second & Title — Second snippet." in out["result"]


def test_empty_query_errors():
    out = json.loads(web_search.dispatch("web_search", {"query": "  "}))
    assert "error" in out


def test_no_results_is_graceful(monkeypatch):
    _patch(monkeypatch, "<html>nothing here</html>")
    out = json.loads(web_search.dispatch("web_search", {"query": "zzz"}))
    assert "couldn't find" in out["result"]


def test_network_failure_is_graceful(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(web_search.urllib.request, "urlopen", boom)
    out = json.loads(web_search.dispatch("web_search", {"query": "x"}))
    assert "error" in out


def test_unknown_tool_errors():
    out = json.loads(web_search.dispatch("nope", {}))
    assert "error" in out
