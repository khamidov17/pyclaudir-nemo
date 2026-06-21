"""Put the voice server dir on sys.path so tests can `import skills` etc.

The voice modules import each other by bare name (the server runs with its own
dir as cwd), so tests need that dir importable. Kept out of the engine's
`testpaths` (pyproject) — run these with `pytest nemo-voice/server/tests`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
