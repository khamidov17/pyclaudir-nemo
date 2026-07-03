"""The fetch SSRF guard only allows http(s).

Before the scheme allowlist, a non-http scheme with a *resolvable* host
(``ftp://internal-host/``, ``gopher://…``) sailed past the hostname/IP checks
and could reach an unexpected sink; only schemes without a hostname
(``file:///…``) were incidentally blocked. The allowlist closes the whole class.
"""

from __future__ import annotations

import pytest

from pyclaudir.security import validate_url_not_ssrf


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/secret",
        "gopher://example.com/",
        "data:text/html,<script>",
        "jar:http://example.com!/",
    ],
)
def test_non_http_schemes_blocked(url: str) -> None:
    err = validate_url_not_ssrf(url)
    assert err is not None and err.startswith("blocked scheme"), err


def test_http_scheme_passes_the_scheme_gate() -> None:
    # A public host clears every check (scheme + IP). Not asserting None
    # unconditionally — DNS could fail in a sandbox — but it must NOT be
    # rejected for its scheme.
    err = validate_url_not_ssrf("https://example.com/")
    assert err is None or not err.startswith("blocked scheme"), err
