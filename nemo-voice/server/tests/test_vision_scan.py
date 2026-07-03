"""vision_scan — receipt→ledger, card→contact, form read, routing, degrade."""

from __future__ import annotations

import json

import pytest

import ledger
import memory_store
import vision
import vision_scan


class FakeBridge:
    def __init__(self, image="imgdata"):
        self.image = image

    async def run(self, cmd, timeout=20.0):
        return {"image_b64": self.image} if self.image else {"error": "no camera"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))


def _vl(monkeypatch, payload: dict | str):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(vision, "describe", lambda *a, **k: raw)


@pytest.mark.asyncio
async def test_receipt_logged_to_ledger(monkeypatch):
    _vl(
        monkeypatch,
        {
            "kind": "receipt",
            "vendor": "Korzinka",
            "total": 85000,
            "currency": "UZS",
            "category": "food",
        },
    )
    out = json.loads(await vision_scan.dispatch("scan", {"kind": "auto"}, FakeBridge()))
    assert "Korzinka" in out["result"] and "85000" in out["result"]
    summary = json.loads(await ledger.dispatch("ledger_summary", {"period": "week"}))
    assert summary["spent"][0]["total"] == 85000


@pytest.mark.asyncio
async def test_card_saved_as_contact(monkeypatch):
    _vl(
        monkeypatch,
        {
            "kind": "card",
            "name": "Aziz Karimov",
            "org": "Uztech",
            "phone": "+998901112233",
            "email": "aziz@uztech.uz",
        },
    )
    out = json.loads(await vision_scan.dispatch("scan", {}, FakeBridge()))
    assert "Aziz Karimov" in out["result"]
    facts = [t for _, _, t in memory_store.live_facts()]
    assert any("Aziz Karimov" in f and "+998901112233" in f for f in facts)


@pytest.mark.asyncio
async def test_form_read_back(monkeypatch):
    _vl(
        monkeypatch,
        {"kind": "form", "summary": "A visa application with name and passport fields"},
    )
    out = json.loads(await vision_scan.dispatch("scan", {"kind": "form"}, FakeBridge()))
    assert "visa application" in out["result"]
    assert "fill" in out["result"].lower()


@pytest.mark.asyncio
async def test_explicit_kind_overrides_detected(monkeypatch):
    # Model says card, but he asked for receipt → treated as receipt (no total → prompt).
    _vl(monkeypatch, {"kind": "card", "vendor": "", "total": 0})
    out = json.loads(
        await vision_scan.dispatch("scan", {"kind": "receipt"}, FakeBridge())
    )
    assert "amount" in out["result"].lower()


@pytest.mark.asyncio
async def test_no_image_degrades():
    out = json.loads(await vision_scan.dispatch("scan", {}, FakeBridge(image="")))
    assert "error" in out


@pytest.mark.asyncio
async def test_unparseable_vl_degrades(monkeypatch):
    _vl(monkeypatch, "sorry I can't read this")
    out = json.loads(await vision_scan.dispatch("scan", {}, FakeBridge()))
    assert "couldn't tell" in out["result"] or "couldn't" in out["result"].lower()


@pytest.mark.asyncio
async def test_no_bridge():
    out = json.loads(await vision_scan.dispatch("scan", {}, None))
    assert "error" in out
