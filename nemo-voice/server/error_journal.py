"""Nemo activity + error journal — voice server side.

Appends one entry per error/warning to data/nemo_error_log.md so the owner
can review what Nemo struggled with each day.  Thread-safe via fcntl exclusive
lock; never raises (logs to stderr instead) so a journal write can never break
the voice pipeline.

Usage:
    from error_journal import log_error, log_warn

    log_error("tool/set_alarm", "IntentActions returned false — no clock app installed")
    log_warn("reminder/kick", "session not found, fell back to phone push")
"""

from __future__ import annotations

import fcntl
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

_LOG = logging.getLogger("nemo.error_journal")

_DATA_DIR = Path(
    os.environ.get(
        "NEMO_VOICE_DATA_DIR",
        os.environ.get("PYCLAUDIR_DATA_DIR", Path(__file__).resolve().parents[2] / "data"),
    )
)
_JOURNAL = _DATA_DIR / "nemo_error_log.md"


def _append(level: str, source: str, message: str, detail: str) -> None:
    try:
        _JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc)
        date_hdr = f"## {ts.strftime('%Y-%m-%d')}\n"
        time_str = ts.strftime("%H:%M:%S UTC")
        line = f"- `{time_str}` **[{level}]** `{source}` — {message}"
        if detail:
            line += f"\n  > {detail}"
        line += "\n"

        with _JOURNAL.open("a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                # Inject date header if this is the first entry for today.
                f.seek(0)
                existing = f.read()
                f.seek(0, 2)
                if date_hdr.strip() not in existing:
                    if existing and not existing.endswith("\n\n"):
                        f.write("\n")
                    f.write(date_hdr)
                f.write(line)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("error_journal write failed: %s", exc)


def log_error(source: str, message: str, detail: str = "") -> None:
    """Log something Nemo tried but couldn't do, or an unexpected failure."""
    _append("ERROR", source, message, detail)


def log_warn(source: str, message: str, detail: str = "") -> None:
    """Log a degraded path that still partially worked."""
    _append("WARN", source, message, detail)
