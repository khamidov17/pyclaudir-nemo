"""Real-time subtitles — spoken conversation mirrored to the screen.

Toggle ("subtitles on" / "caption this", off: "subtitles off"). While on, the
pump emits a ``{"type":"subtitle","who":…,"text":…}`` event for each user
transcript and each of Nemo's replies, so the app can render captions — useful
in a loud room, for the hard-of-hearing, or to read a translation on screen.

State only; the pump owns the emit. See docs/design-vision-health.md.
"""

from __future__ import annotations

import re

_ON = re.compile(
    r"\b(subtitles?\s+on|caption\s+(this|mode|me)|show\s+subtitles?|"
    r"subtitr(ni)?\s+yoq)\b",
    re.I,
)
_OFF = re.compile(
    r"\b(subtitles?\s+off|stop\s+caption(ing|s)?|hide\s+subtitles?|"
    r"subtitr(ni)?\s+o'chir)\b",
    re.I,
)


def on_intent(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 6 and bool(_ON.search(t))


def is_off_intent(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 6 and bool(_OFF.search(t))
