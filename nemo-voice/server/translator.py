"""Translator mode — Nemo as a strict two-way interpreter.

Avazbek ↔ foreign speaker, each hearing their own language. While
interpreting, Nemo adds NOTHING of his own — the single exception is an
*aside*: Avazbek addressing Nemo by name gets a direct answer (in his
language, never translated to the other person), then interpreting resumes.

Direction comes from two signals (either suffices): the language of the
utterance, and the speaker-gate verdict injected as a per-turn hint.
See docs/design-translator-nav.md.
"""

from __future__ import annotations

import re

_ON = re.compile(
    r"\b(translat(or|e|ion)\s+(mode|rejim)|interpreter\s+mode|"
    r"tarjimon\s+rejimi?|tarjima\s+qil(ib\s+tur)?)\b",
    re.I,
)
_OFF = re.compile(
    r"\b(stop\s+translat(ing|or|ion)|translator\s+off|end\s+translation|"
    r"tarjima\s+(tugadi|bo'ldi)|tarjimon\s+o'chir)\b",
    re.I,
)

# "translator mode for chinese" / "tarjimon rejimi xitoycha" → target language.
_LANGS = {
    "chinese": "Chinese (Mandarin)",
    "mandarin": "Chinese (Mandarin)",
    "xitoy": "Chinese (Mandarin)",
    "english": "English",
    "ingliz": "English",
    "russian": "Russian",
    "rus": "Russian",
    "korean": "Korean",
    "koreys": "Korean",
    "japanese": "Japanese",
    "yapon": "Japanese",
    "turkish": "Turkish",
    "turk": "Turkish",
    "arabic": "Arabic",
    "arab": "Arabic",
    "german": "German",
    "nemis": "German",
    "french": "French",
    "fransuz": "French",
    "spanish": "Spanish",
    "ispan": "Spanish",
}
_DEFAULT_LANG = "Chinese (Mandarin)"

OWNER_LANG = "Avazbek's language (reply in whichever of Uzbek/English/Russian he has been speaking)"


def on_intent(text: str) -> str | None:
    """Target language if this utterance turns translator mode on, else None."""
    t = (text or "").strip()
    if len(t) < 6 or not _ON.search(t):
        return None
    lowered = t.lower()
    for key, lang in _LANGS.items():
        if key in lowered:
            return lang
    return _DEFAULT_LANG


def is_off_intent(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 6 and bool(_OFF.search(t))


def instructions(target_lang: str) -> str:
    """The interpreter system prompt — replaces the persona for the session."""
    return (
        "You are now a professional simultaneous INTERPRETER between Avazbek "
        f"and a {target_lang} speaker. Rules, absolute:\n"
        f"1. Speech in {target_lang} → say it in {OWNER_LANG}. Speech in "
        f"Avazbek's language → say it in {target_lang}.\n"
        "2. Translate faithfully and naturally — meaning and tone, not word by "
        "word. First person, as if the speaker said it. NEVER add your own "
        "commentary, answers, or opinions. NEVER answer the other speaker's "
        "questions yourself — just render them for Avazbek.\n"
        "3. THE ONE EXCEPTION: each turn carries a bracketed hint. When it "
        "marks an ASIDE (Avazbek addressing you by name), answer HIM directly, "
        "briefly, in his language — and do not translate that exchange. Then "
        "resume interpreting.\n"
        "4. If you couldn't hear something clearly, say so in Avazbek's "
        "language in three words or fewer, don't guess.\n"
        "5. No greetings, no meta-talk about translating. Just interpret."
    )


def turn_hint(target_lang: str, owner_voice: bool | None, aside: bool) -> str:
    """The per-turn context item that pins direction. ``owner_voice`` is the
    voiceprint signal: True → outbound, False → inbound, None → unknown (gate
    off) so the hint stays neutral and the language rule decides."""
    if aside:
        return (
            "[ASIDE — Avazbek is asking YOU directly. Answer him briefly in "
            "his language. Do NOT translate this exchange to the other person.]"
        )
    if owner_voice is None:
        return (
            f"[Translate this turn: if it is in {target_lang}, render it in "
            f"{OWNER_LANG}; if it is in Avazbek's language, render it in "
            f"{target_lang}. Do not answer it — just translate.]"
        )
    if owner_voice:
        return f"[Avazbek speaking → render this in {target_lang}.]"
    return (
        f"[Other speaker → render this in {OWNER_LANG}. Do not answer it — "
        "just translate.]"
    )
