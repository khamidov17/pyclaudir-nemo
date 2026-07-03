"""ledger — logging, SQL summaries, period/category filters, validation."""

from __future__ import annotations

import json

import pytest

import ledger


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMO_VOICE_DATA_DIR", str(tmp_path))


@pytest.mark.asyncio
async def test_expense_logged_and_summed():
    await ledger.dispatch("log_expense", {"amount": 50000, "category": "food"})
    await ledger.dispatch("log_expense", {"amount": 20000, "category": "food"})
    await ledger.dispatch("log_expense", {"amount": 15000, "category": "transport"})
    out = json.loads(await ledger.dispatch("ledger_summary", {"period": "week"}))
    assert out["spent"][0]["total"] == 85000
    assert out["spent"][0]["currency"] == "UZS"
    assert out["top_categories"][0] == {
        "category": "food",
        "currency": "UZS",
        "total": 70000,
    }


@pytest.mark.asyncio
async def test_top_categories_split_by_currency():
    await ledger.dispatch("log_expense", {"amount": 50000, "category": "food"})
    await ledger.dispatch(
        "log_expense", {"amount": 20, "category": "food", "currency": "USD"}
    )
    out = json.loads(await ledger.dispatch("ledger_summary", {"period": "week"}))
    cats = {(c["category"], c["currency"]): c["total"] for c in out["top_categories"]}
    assert cats[("food", "UZS")] == 50000 and cats[("food", "USD")] == 20


@pytest.mark.asyncio
async def test_non_numeric_amount_does_not_crash():
    out = json.loads(
        await ledger.dispatch("log_expense", {"amount": "lots", "category": "x"})
    )
    assert "error" in out  # coerced to 0 → rejected, no ValueError


@pytest.mark.asyncio
async def test_category_filter():
    await ledger.dispatch("log_expense", {"amount": 50000, "category": "food"})
    await ledger.dispatch("log_expense", {"amount": 90000, "category": "shopping"})
    out = json.loads(
        await ledger.dispatch("ledger_summary", {"period": "week", "category": "food"})
    )
    assert out["spent"][0]["total"] == 50000


@pytest.mark.asyncio
async def test_habits_counted():
    await ledger.dispatch("log_habit", {"name": "gym"})
    await ledger.dispatch("log_habit", {"name": "gym"})
    await ledger.dispatch("log_habit", {"name": "sleep", "amount": 6})
    out = json.loads(await ledger.dispatch("ledger_summary", {"period": "week"}))
    habits = {h["name"]: h for h in out["habits"]}
    assert habits["gym"]["times"] == 2
    assert habits["sleep"]["total_amount"] == 6


@pytest.mark.asyncio
async def test_invalid_inputs_rejected():
    out = json.loads(
        await ledger.dispatch("log_expense", {"amount": -5, "category": "x"})
    )
    assert "error" in out
    out = json.loads(await ledger.dispatch("log_habit", {}))
    assert "error" in out


@pytest.mark.asyncio
async def test_foreign_currency_kept_separate():
    await ledger.dispatch(
        "log_expense", {"amount": 20, "category": "food", "currency": "USD"}
    )
    await ledger.dispatch("log_expense", {"amount": 50000, "category": "food"})
    out = json.loads(await ledger.dispatch("ledger_summary", {"period": "week"}))
    assert {s["currency"] for s in out["spent"]} == {"USD", "UZS"}
