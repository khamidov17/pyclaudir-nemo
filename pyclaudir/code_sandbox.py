"""Run untrusted code in an isolated cloud sandbox (E2B or sandbox0).

The engine's host bash/code tools are disabled (the RCE lockdown), so code runs
in a throwaway cloud sandbox instead — never on the box that holds the keys.

Two providers, picked by ``CODE_SANDBOX_PROVIDER`` or auto-detected from whichever
token is set:
  * **E2B** (e2b.dev) — open self-serve, instant key. ``E2B_API_KEY`` +
    ``pip install e2b-code-interpreter``.
  * **sandbox0** (sandbox0.ai) — waitlist. ``SANDBOX0_TOKEN`` + ``pip install sandbox0``.

Both SDKs are OPTIONAL deps imported lazily inside their backend, so the engine
starts fine without either. The result extraction is defensive because the SDK
return shapes aren't fully pinned — verify against a real key when enabling.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

_SANDBOX0_TTL_SEC = int(os.environ.get("SANDBOX0_TTL_SEC", "300"))
_SANDBOX0_TEMPLATE = os.environ.get("SANDBOX0_TEMPLATE", "default")
# Hard wall-clock cap on a single run so an infinite loop can't pin a worker
# thread (and burn the sandbox's billed time) indefinitely.
_TIMEOUT_SEC = int(os.environ.get("CODE_SANDBOX_TIMEOUT_SEC", "60"))
# Spawn cap: at most this many sandboxes per rolling minute, so a looping or
# poisoned task can't fan out unlimited (billed) sandboxes.
_MAX_PER_MIN = int(os.environ.get("CODE_SANDBOX_MAX_PER_MIN", "20"))
_recent_spawns: list[float] = []
# Sandbox outbound internet. Default on (real coding tasks fetch data / pip),
# but exposed so a stricter deployment can cut egress — the sandbox isolates the
# HOST, not the network, so a poisoned task could otherwise phone home from it.
_ALLOW_NET = os.environ.get("CODE_SANDBOX_ALLOW_NET", "1").strip() not in ("0", "false")


@dataclass
class SandboxResult:
    ok: bool
    output: str


def _rate_limited() -> bool:
    now = time.monotonic()
    _recent_spawns[:] = [t for t in _recent_spawns if now - t < 60.0]
    if len(_recent_spawns) >= _MAX_PER_MIN:
        return True
    _recent_spawns.append(now)
    return False


def provider() -> str | None:
    """Which backend is configured — explicit env wins, else auto-detect."""
    explicit = os.environ.get("CODE_SANDBOX_PROVIDER", "").strip().lower()
    if explicit in ("e2b", "sandbox0"):
        return explicit
    if os.environ.get("E2B_API_KEY", "").strip():
        return "e2b"
    if os.environ.get("SANDBOX0_TOKEN", "").strip():
        return "sandbox0"
    return None


def available() -> bool:
    """True when a sandbox provider is configured (else the tool stays inert)."""
    return provider() is not None


def run_code_sync(language: str, code: str) -> SandboxResult:
    """Open a fresh sandbox, run ``code``, return its combined output.

    Blocking (the SDKs are synchronous) — call via ``asyncio.to_thread``. Raises
    on SDK/token/network failure; the caller turns that into a tool error.
    """
    prov = provider()
    if prov is None:
        return SandboxResult(
            False, "code sandbox not configured (set E2B_API_KEY or SANDBOX0_TOKEN)"
        )
    if _rate_limited():
        return SandboxResult(False, "too many sandboxes — slow down")
    if prov == "e2b":
        return _run_e2b(language, code)
    return _run_sandbox0(language, code)


def _run_e2b(language: str, code: str) -> SandboxResult:
    from e2b_code_interpreter import Sandbox  # lazy: optional dependency

    # v2 API: Sandbox.create() (the bare constructor is deprecated). `timeout` is
    # the sandbox lifetime; allow_internet_access gates egress.
    with Sandbox.create(
        timeout=_TIMEOUT_SEC, allow_internet_access=_ALLOW_NET
    ) as sandbox:
        if language in ("bash", "sh"):
            cmd = sandbox.commands.run(code, timeout=_TIMEOUT_SEC)
            out = (getattr(cmd, "stdout", "") or "") + (
                getattr(cmd, "stderr", "") or ""
            )
            return SandboxResult(
                getattr(cmd, "exit_code", 0) == 0, out or "(no output)"
            )
        lang = "js" if language == "node" else language
        return _from_e2b_execution(sandbox.run_code(code, language=lang))


def _from_e2b_execution(execution: object) -> SandboxResult:
    """Pull stdout / result text / error out of an E2B Execution object."""
    err = getattr(execution, "error", None)
    if err:
        return SandboxResult(
            False, f"{getattr(err, 'name', 'error')}: {getattr(err, 'value', err)}"
        )
    logs = getattr(execution, "logs", None)
    stdout = "".join(getattr(logs, "stdout", []) or []) if logs else ""
    text = getattr(execution, "text", None)
    return SandboxResult(True, stdout or (str(text) if text else "") or "(no output)")


def _run_sandbox0(language: str, code: str) -> SandboxResult:
    from sandbox0 import Client  # lazy: optional dependency
    from sandbox0.apispec.models.sandbox_config import SandboxConfig

    client = Client(token=os.environ["SANDBOX0_TOKEN"].strip())
    with client.sandboxes.open(
        _SANDBOX0_TEMPLATE, config=SandboxConfig(ttl=_SANDBOX0_TTL_SEC)
    ) as sandbox:
        result = sandbox.run(language, code)
    return SandboxResult(True, _stringify(result))


def _stringify(result: object) -> str:
    """Defensive output extraction for sandbox0's undocumented run() return."""
    if result is None:
        return "(no output)"
    for attr in ("output", "stdout", "text", "result", "logs"):
        val = getattr(result, attr, None)
        if val:
            return str(val)
    return str(result)
