"""persona — character traits present, constraints intact, prompt composition."""

from __future__ import annotations

import persona
import voice_brain


def test_friday_base_not_helpdesk():
    p = persona.prompt()
    assert "FRIDAY" in p
    assert "NOT ChatGPT" in p
    assert "help desk" in p


def test_identity_constraints_intact():
    p = persona.prompt()
    assert "created by Avazbek" in p
    assert "simply Nemo" in p
    assert "BREVITY" in p


def test_code_switching_rules():
    p = persona.prompt()
    assert "Uzbek" in p and "Russian" in p
    assert "code-switch" in p
    assert "Never assume Chinese" in p


def test_warmth_and_banter():
    p = persona.prompt()
    assert "tease" in p
    assert "Banter" in p or "banter" in p
    assert "opinions" in p


def test_extra_fragment_appended(monkeypatch):
    monkeypatch.setenv("VOICE_PERSONA_EXTRA", "Call him boss on Fridays.")
    assert persona.prompt().rstrip().endswith("Call him boss on Fridays.")


def test_build_prompt_leads_with_persona(monkeypatch, tmp_path):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))
    prompt = voice_brain.build_prompt(seed_history=True)
    assert prompt.startswith("You are Nemo")
    assert "FRIDAY" in prompt
