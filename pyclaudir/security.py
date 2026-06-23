from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import threading
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

KILL_MARKER_FILENAME = "kill_marker"

_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("100.64.0.0/10"),
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
]

_SENSITIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI/Anthropic API key", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("Slack token", re.compile(r"xoxb-[A-Za-z0-9\-]+")),
    ("GitHub PAT", re.compile(r"gh[pousr]_[A-Za-z0-9]{36}")),
    ("Google API key", re.compile(r"AIza[A-Za-z0-9_\-]{35}")),
    ("localhost with port", re.compile(r"127\.0\.0\.1:\d+")),
    ("injection marker", re.compile(r"ANTHROPIC_MAGIC_STRING_")),
]


# Only ever fetch over HTTP(S). An explicit allowlist closes off file://,
# gopher://, ftp://, data:// and similar schemes that can reach local files or
# unexpected sinks — defence in depth alongside the IP checks below.
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _is_blocked_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _BLOCKED_NETWORKS)


def _check_hostname_ip_literal(hostname: str) -> str | None:
    stripped = hostname.strip("[]")
    try:
        ip = ipaddress.ip_address(stripped)
    except ValueError:
        return None
    if any(ip in net for net in _BLOCKED_NETWORKS):
        return f"blocked IP literal {ip}"
    return None


def _resolve_and_check(hostname: str) -> str | None:
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        log.debug("DNS resolution failed for %s: %s", hostname, exc)
        return f"DNS resolution failed: {exc}"
    for family, _type, _proto, _canonname, sockaddr in results:
        addr = sockaddr[0]
        if _is_blocked_ip(addr):
            return f"resolved to blocked address {addr}"
    return None


def validate_url_not_ssrf(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return f"invalid URL: {exc}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return f"blocked scheme: {scheme or '(none)'}"

    hostname = parsed.hostname
    if not hostname:
        return "missing hostname"

    literal_error = _check_hostname_ip_literal(hostname)
    if literal_error:
        return literal_error

    dns_error = _resolve_and_check(hostname)
    if dns_error:
        return dns_error

    return None


async def validate_url_not_ssrf_async(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return f"invalid URL: {exc}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return f"blocked scheme: {scheme or '(none)'}"

    hostname = parsed.hostname
    if not hostname:
        return "missing hostname"

    literal_error = _check_hostname_ip_literal(hostname)
    if literal_error:
        return literal_error

    dns_error = await asyncio.to_thread(_resolve_and_check, hostname)
    return dns_error


class SendRateLimiter:
    def __init__(
        self,
        max_messages: int = 20,
        window_seconds: float = 60.0,
    ) -> None:
        self._max = max_messages
        self._window = window_seconds
        self._lock = threading.Lock()
        self._timestamps: dict[int, list[float]] = defaultdict(list)

    def check_and_record(self, chat_id: int) -> bool:
        import time

        now = time.monotonic()
        cutoff = now - self._window

        with self._lock:
            bucket = self._timestamps[chat_id]
            self._timestamps[chat_id] = [t for t in bucket if t >= cutoff]
            if len(self._timestamps[chat_id]) >= self._max:
                log.warning("Rate limit exceeded for chat_id=%d", chat_id)
                return False
            self._timestamps[chat_id].append(now)
            return True


def check_outbound_sensitive(text: str) -> str | None:
    for description, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(text):
            log.warning("Sensitive content matched pattern: %s", description)
            return description
    return None


def write_kill_marker(data_dir: Path) -> None:
    marker = data_dir / KILL_MARKER_FILENAME
    try:
        marker.touch(exist_ok=True)
        log.info("Kill marker written to %s", marker)
    except OSError as exc:
        log.error("Failed to write kill marker: %s", exc)
        raise


def kill_marker_exists(data_dir: Path) -> bool:
    return (data_dir / KILL_MARKER_FILENAME).exists()


def clear_kill_marker(data_dir: Path) -> None:
    marker = data_dir / KILL_MARKER_FILENAME
    try:
        marker.unlink(missing_ok=True)
        log.info("Kill marker cleared at %s", marker)
    except OSError as exc:
        log.error("Failed to clear kill marker: %s", exc)
        raise
