"""Put the gateway dir on sys.path, mirroring the voice server's test setup."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
