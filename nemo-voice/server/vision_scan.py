"""Structured document capture — receipts, business cards, forms.

`scan` snaps the camera (like `look`) but runs a JSON-extraction prompt and
ACTS on the result: a receipt is logged to the ledger, a business card is
saved as a contact fact, a form is read back. Falls back to a plain
description when the model can't parse or no key is set — never crashes.

Owner-only (writes ledger + memory): listed in pump_tools._PROTECTED_TOOLS.
See docs/design-vision-health.md.
"""

from __future__ import annotations

import asyncio
import json
import logging

import ledger
import memory_store
import vision

LOG = logging.getLogger("nemo.vision_scan")

_KINDS = ("receipt", "card", "form", "auto")

FUNCTIONS: list[dict] = [
    {
        "name": "scan",
        "description": (
            "Snap the camera at a document and act on it: a RECEIPT is logged to "
            "his expenses automatically, a business CARD is saved as a contact, a "
            "FORM is read back to him. Use when he says 'scan this', 'log this "
            "receipt', 'save this card', 'read this form'. Default kind=auto "
            "detects which it is."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(_KINDS),
                    "description": "receipt | card | form | auto (default).",
                }
            },
        },
    }
]
TOOL_NAMES = {f["name"] for f in FUNCTIONS}

_PROMPT = (
    "Look at this photo of a document. Reply with ONLY a JSON object:\n"
    '{"kind": "receipt"|"card"|"form"|"other",\n'
    ' "vendor": str, "total": number, "currency": str, "category": one of '
    "[food, transport, shopping, bills, fun, health, other],\n"
    ' "name": str, "org": str, "phone": str, "email": str,\n'
    ' "summary": short str describing the document and, for a form, its fields}\n'
    "Fill only the fields that apply to the detected kind; use null/empty for "
    "the rest. total is a plain number (no thousands separators)."
)


def _parse(raw: str) -> dict:
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
        return data if isinstance(data, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def _handle_receipt(data: dict) -> str:
    total = float(data.get("total") or 0)
    vendor = str(data.get("vendor") or "").strip()
    if total <= 0:
        return json.dumps(
            {
                "result": f"Looks like a receipt from {vendor or 'a shop'} but I couldn't read the total — tell me the amount and I'll log it."
            }
        )
    ledger._add(
        ledger.Entry(
            kind="expense",
            amount=total,
            category=str(data.get("category") or "other"),
            currency=str(data.get("currency") or ""),
            note=f"receipt: {vendor}"[:200],
        )
    )
    cur = str(data.get("currency") or "").strip()
    return json.dumps(
        {
            "result": f"Logged {total:g} {cur} at {vendor or 'the shop'} to your expenses."
        }
    )


def _handle_card(data: dict) -> str:
    name = str(data.get("name") or "").strip()
    if not name:
        return json.dumps({"result": "I see a card but couldn't read a name clearly."})
    bits = [f"Contact — {name}"]
    for label, key in (("org", "org"), ("phone", "phone"), ("email", "email")):
        val = str(data.get(key) or "").strip()
        if val:
            bits.append(f"{label}: {val}")
    memory_store.add_fact(
        "; ".join(bits), subject=f"contact:{name}", source="scan_card"
    )
    return json.dumps({"result": f"Saved {name}'s contact."})


async def dispatch(name: str, args: dict, bridge) -> str:
    if name != "scan":
        return json.dumps({"error": f"unknown scan tool {name}"})
    if bridge is None:
        return json.dumps({"error": "phone not connected"})
    want = str(args.get("kind") or "auto").strip().lower()
    result = await bridge.run("camera", timeout=20.0)
    image_b64 = result.get("image_b64") or result.get("imageB64")
    if not image_b64:
        return json.dumps(
            {"error": result.get("error") or "couldn't capture the document"}
        )
    raw = await asyncio.to_thread(vision.describe, image_b64, _PROMPT, "jpeg", 400)
    if not raw:
        return json.dumps(
            {"result": "I couldn't read the document clearly — try better light."}
        )
    data = _parse(raw)
    kind = want if want != "auto" else str(data.get("kind") or "other")
    if kind == "receipt":
        return _handle_receipt(data)
    if kind == "card":
        return _handle_card(data)
    if kind == "form":
        summary = str(data.get("summary") or "").strip()
        return json.dumps(
            {"result": f"{summary or 'A form.'} Want me to help you fill it in?"}
        )
    return json.dumps(
        {
            "result": str(
                data.get("summary") or "I couldn't tell what kind of document that is."
            )
        }
    )
