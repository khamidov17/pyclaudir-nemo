"""Gemini Live bridge — alternative voice backend to the Deepgram one.

Speaks the SAME app protocol as streaming_service (auth handled by the caller;
app sends 16k PCM audio, receives 24k PCM audio + ready/agent_audio_start/audio/
turn_complete/interrupted/user_transcript/text/error) so the Flutter app needs
zero changes. Reuses voice_brain for Nemo's identity, shared memory and tools,
so personality + memory carry over identically. Selected via VOICE_BACKEND=gemini.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

from google import genai
from google.genai import types

import voice_brain
from action_bridge import ActionBridge

LOG = logging.getLogger("nemo.gemini_voice")

MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-2.0-flash-live-001")
VOICE = os.environ.get("GEMINI_VOICE", "Aoede")


def _function_declarations() -> list[types.FunctionDeclaration]:
    """Reuse voice_brain's tool schemas verbatim (remember/recall/search_chat/…)."""
    return [
        types.FunctionDeclaration(
            name=f["name"],
            description=f["description"],
            parameters_json_schema=f.get("parameters") or {"type": "object", "properties": {}},
        )
        for f in voice_brain.FUNCTIONS
    ]


def _config() -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=types.Content(parts=[types.Part(text=voice_brain.build_prompt())]),
        tools=[types.Tool(function_declarations=_function_declarations())],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE),
            ),
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )


async def run_session(client_ws) -> None:
    """Bridge one authenticated app client to a Gemini Live session."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        await client_ws.send(json.dumps({"type": "error", "message": "GEMINI_API_KEY not configured"}))
        return
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    async with client.aio.live.connect(model=MODEL, config=_config()) as session:
        LOG.info("Gemini Live session open (model=%s, voice=%s)", MODEL, VOICE)
        await client_ws.send(json.dumps({"type": "ready"}))
        bridge = ActionBridge(client_ws)
        to_gemini = asyncio.create_task(_recv_client(client_ws, session, bridge))
        to_client = asyncio.create_task(_recv_gemini(session, client_ws, bridge))
        done, pending = await asyncio.wait(
            {to_gemini, to_client}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()


async def _recv_client(client_ws, session, bridge) -> None:
    async for raw in client_ws:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg_type = data.get("type")
        if msg_type == "action_result":
            bridge.resolve(data)
        elif msg_type == "audio":
            audio = base64.b64decode(data.get("data", ""))
            if audio:
                await session.send_realtime_input(
                    audio=types.Blob(data=audio, mime_type="audio/pcm;rate=16000"),
                )
        elif msg_type == "inject":
            text = data.get("text", "")
            if text:
                await session.send_realtime_input(text=text)


async def _recv_gemini(session, client_ws, bridge) -> None:
    agent_started = False
    while True:
        # session.receive() yields one turn then ends; re-enter for the next
        # turn. If the iterator completes without yielding, the session is
        # gone — exit instead of spinning on exhausted iterators forever.
        got_message = False
        async for msg in session.receive():
            got_message = True
            if msg.tool_call:
                await _handle_tool_calls(msg.tool_call, session, bridge)
            sc = msg.server_content
            if not sc:
                continue
            if sc.input_transcription and sc.input_transcription.text:
                await client_ws.send(json.dumps(
                    {"type": "user_transcript", "data": sc.input_transcription.text}
                ))
            if sc.output_transcription and sc.output_transcription.text:
                await client_ws.send(json.dumps(
                    {"type": "text", "data": sc.output_transcription.text}
                ))
            if sc.model_turn:
                for part in sc.model_turn.parts:
                    inline = getattr(part, "inline_data", None)
                    if inline and inline.data:
                        if not agent_started:
                            agent_started = True
                            await client_ws.send(json.dumps({"type": "agent_audio_start"}))
                        await client_ws.send(json.dumps({
                            "type": "audio",
                            "data": base64.b64encode(inline.data).decode("ascii"),
                        }))
            if sc.interrupted:
                agent_started = False
                await client_ws.send(json.dumps({"type": "interrupted", "data": "barge_in"}))
            if sc.turn_complete and agent_started:
                agent_started = False
                await client_ws.send(json.dumps({"type": "turn_complete"}))
        if not got_message:
            LOG.info("Gemini session closed (receive yielded nothing)")
            return


async def _handle_tool_calls(tool_call, session, bridge) -> None:
    responses = []
    for fc in tool_call.function_calls:
        args = dict(fc.args or {})
        LOG.info("gemini function call: %s %s", fc.name, args)
        content = await voice_brain.dispatch(fc.name, args, bridge)
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            result = {"result": content}
        responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response=result))
    await session.send_tool_response(function_responses=responses)
