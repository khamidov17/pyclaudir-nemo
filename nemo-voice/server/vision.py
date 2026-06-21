"""Camera + screen vision for the voice agent via Qwen-VL.

When Avazbek asks Nemo to LOOK at something — "what is this?", "translate this
menu", "read this label", "who is this?" — the `look` tool snaps the camera (or
the screen), sends the image to DashScope Qwen-VL with his question, and returns
a short answer for Nemo to speak. It only ever fires on an explicit request, so
it fits the read-only-when-asked rule.

Stdlib-only (urllib); reuses the same DASHSCOPE_API_KEY as the realtime voice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request

LOG = logging.getLogger("nemo.vision")

_VL_URL = os.environ.get(
    "QWEN_VL_URL",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
)
_VL_MODEL = os.environ.get("QWEN_VL_MODEL", "qwen-vl-max")

TOOL_NAMES = {"look"}

FUNCTIONS: list[dict] = [
    {
        "name": "look",
        "description": (
            "LOOK through Avazbek's phone and answer about what you see. Use the "
            "moment he asks you to see / read / translate / identify something — "
            "'what is this?', 'translate this', 'read this label', 'who is this?'. "
            "Snaps the CAMERA by default; set use_screen=true to read his screen. "
            "You can describe a person (clothing, setting, mood) but never claim to "
            "know a stranger's real identity. Keep your answer short and natural."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What he wants to know about what you see.",
                },
                "use_screen": {
                    "type": "boolean",
                    "description": "True to read the phone screen instead of the camera.",
                },
            },
            "required": ["question"],
        },
    }
]


def _ask_vl(image_b64: str, question: str, mime: str) -> str | None:
    """Send one image + question to Qwen-VL; return the text answer or None."""
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        return None
    body = json.dumps(
        {
            "model": _VL_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": question
                            + " Answer briefly and naturally for Avazbek, in English.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/{mime};base64,{image_b64}"
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 300,
        }
    ).encode()
    req = urllib.request.Request(
        _VL_URL,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001 — never crash the voice turn
        LOG.warning("Qwen-VL request failed: %s", exc)
        return None


async def dispatch(name: str, args: dict, bridge) -> str:
    """Capture an image from the phone and answer Avazbek's question about it."""
    if name != "look":
        return json.dumps({"error": f"unknown vision tool {name}"})
    if bridge is None:
        return json.dumps({"error": "phone not connected"})
    question = (args.get("question") or "").strip() or "What do you see?"
    use_screen = bool(args.get("use_screen"))
    cmd, mime = ("screenshot", "png") if use_screen else ("camera", "jpeg")
    result = await bridge.run(cmd, timeout=20.0)
    image_b64 = result.get("image_b64") or result.get("imageB64")
    if not image_b64:
        return json.dumps(
            {"error": result.get("error") or "couldn't capture an image to look at"}
        )
    answer = await asyncio.to_thread(_ask_vl, image_b64, question, mime)
    return json.dumps({"result": answer or "I couldn't quite make out the image."})
