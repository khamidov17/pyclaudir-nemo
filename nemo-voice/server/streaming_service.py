from core_utils import (
    BaseStreamServer,
    stream_logger,
    MODEL,
    VOICE_NAME,
    PORT,
    SEND_SAMPLE_RATE,
    SYSTEM_INSTRUCTION
)
import asyncio
import json
import base64
import os
import traceback

# Import Google ADK components
from google.adk.agents import Agent, LiveRequestQueue
from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types
from dotenv import load_dotenv
from google.adk.tools import google_search
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioServerParameters

# Import Nemo's tools — split by auth requirement
from nemo_tools import (
    # Public tools (anyone can use)
    get_current_time,
    fetch_url,
    # Authenticated tools (need Telegram user)
    NEMO_TOOLS,
    set_session_user,
)

load_dotenv()

# Public tools — available to everyone (anonymous + authenticated)
PUBLIC_TOOLS = [
    google_search,
    get_current_time,
    fetch_url,
]

# Authenticated instruction appendix — added when user is logged in via Telegram
AUTHENTICATED_ADDENDUM = """
# Authenticated Session — Full Access

This user is connected via Telegram. You have FULL access to all tools:

- **Telegram**: Send messages and files to the user's DM
- **Memory**: Read/write persistent notes about this user
- **Reminders**: Set/list/cancel scheduled reminders
- **Database**: Query message history and user info
- **Documents**: Create PDFs, Word docs and send to their Telegram DM
- **Gmail**: Read, search, send, reply, forward emails
- **Calendar**: View, create, update, delete events
- **Drive**: Search, list, create, share files
- **Slack**: Read and send messages
- **Notion**: Search pages
- **Outlook**: Read/send mail, view events
- **Canvas LMS**: View courses, assignments, grades
- **Screenshots**: Capture web pages

When creating files or documents, send them to the user's Telegram DM using the chat_id provided.
When saving conversation notes, use the user's ID for memory files.
"""

ANONYMOUS_ADDENDUM = """
# Anonymous Session

This user visited speakupai.uz directly (not via Telegram).
You can chat normally with full personality, answer questions, search the web, and tell the time.

You do NOT have access to personal tools (email, calendar, drive, reminders, messaging).
If they ask for personal features, tell them:
"To use my full powers — email, calendar, reminders, and more — open me through Telegram! I'm @nemo_assistantbot."
"""


def _build_google_maps_mcp():
    """Build Google Maps MCP toolset if API key is available."""
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        return None
    return MCPToolset(
        connection_params=StdioServerParameters(
            command='npx',
            args=["-y", "@modelcontextprotocol/server-google-maps"],
            env={"GOOGLE_MAPS_API_KEY": key}
        ),
    )


class StreamingService(BaseStreamServer):
    """Nemo Voice — Real-time streaming voice assistant with auth-aware tools."""

    def __init__(self, host="0.0.0.0", port=None):
        super().__init__(host, port or PORT)
        self.session_service = InMemorySessionService()
        self.maps_mcp = _build_google_maps_mcp()
        if not self.maps_mcp:
            stream_logger.warning("GOOGLE_MAPS_API_KEY not set - Maps tools disabled")

    def _create_agent(self, authenticated=False, user_context=None):
        """Create an ADK agent with appropriate tools based on auth status."""
        tools = list(PUBLIC_TOOLS)

        if authenticated:
            # Add all Nemo tools for authenticated users
            tools.extend(NEMO_TOOLS)

        # Add Maps MCP if available
        if self.maps_mcp:
            tools.append(self.maps_mcp)

        # Build dynamic instruction
        instruction = SYSTEM_INSTRUCTION
        if authenticated and user_context:
            instruction += AUTHENTICATED_ADDENDUM
            if user_context.get("user_name"):
                instruction += f"\nThe user's name is {user_context['user_name']}. Use their name naturally sometimes.\n"
            if user_context.get("user_id"):
                instruction += f"Their Telegram user_id / chat_id for DMs is: {user_context['user_id']}\n"
            if user_context.get("personality"):
                instruction += f"\n## Your persistent memory (README.md):\n{user_context['personality']}\n"
            if user_context.get("user_memory"):
                instruction += f"\n## What you know about this user:\n{user_context['user_memory']}\n"
            if user_context.get("recent_messages"):
                instruction += f"\n## Recent text chat (for context):\n{user_context['recent_messages']}\n"
        else:
            instruction += ANONYMOUS_ADDENDUM

        return Agent(
            name="nemo_voice_agent",
            model=MODEL,
            instruction=instruction,
            tools=tools,
        )

    async def handle_stream(self, websocket, client_id):
        """Process real-time data streams from the client."""
        self.active_connections[client_id] = websocket

        # Wait briefly for auth message before creating agent
        user_context = None
        authenticated = False

        # Listen for first message — could be 'auth' or 'audio'
        audio_queue = asyncio.Queue()
        video_queue = asyncio.Queue()

        # Collect early messages while waiting for potential auth
        try:
            first_msg = await asyncio.wait_for(websocket.recv(), timeout=3.0)
            data = json.loads(first_msg)
            if data.get("type") == "auth":
                # Validate token against NEMO_APP_TOKEN — reject if wrong
                import hmac as _hmac
                expected = os.environ.get("NEMO_APP_TOKEN", "")
                provided = data.get("token", "")
                if not expected or not _hmac.compare_digest(provided, expected):
                    stream_logger.warning("Voice auth rejected: bad token from client %s", client_id)
                    await websocket.send(json.dumps({"type": "error", "message": "unauthorized"}))
                    await websocket.close()
                    return
                # Identity is derived from the validated token/device — never
                # trusted from client JSON (prevents identity spoofing).
                owner_id = int(os.environ.get("NEMO_DEFAULT_CHAT_ID", "0"))
                user_context = {
                    "user_id": owner_id,
                    "user_name": "Avazbek",
                    "user_memory": data.get("user_memory", ""),
                    "personality": data.get("personality", ""),
                    "recent_messages": data.get("recent_messages", ""),
                }
                authenticated = True
                set_session_user(user_context['user_id'])
                stream_logger.info(f"Authenticated user: {user_context['user_name']} (id={user_context['user_id']})")
            else:
                # Any first message that isn't valid auth is rejected
                stream_logger.warning("Voice rejected: no auth as first message (type=%s)", data.get("type"))
                await websocket.send(json.dumps({"type": "error", "message": "unauthorized"}))
                await websocket.close()
                return
        except asyncio.TimeoutError:
            stream_logger.warning("Auth timeout — rejecting connection %s", client_id)
            await websocket.send(json.dumps({"type": "error", "message": "auth timeout"}))
            await websocket.close()
            return

        # Create agent with appropriate tools
        agent = self._create_agent(authenticated, user_context)

        # Create session
        user_id = f"user_{user_context['user_id'] if user_context else client_id}"
        session_id = f"session_{client_id}"
        await self.session_service.create_session(
            app_name="nemo_voice",
            user_id=user_id,
            session_id=session_id,
        )

        runner = Runner(
            app_name="nemo_voice",
            agent=agent,
            session_service=self.session_service,
        )

        live_request_queue = LiveRequestQueue()

        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=VOICE_NAME
                    )
                )
            ),
            response_modalities=["AUDIO"],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
        )

        async with asyncio.TaskGroup() as tg:
            async def receive_client_messages():
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        if data.get("type") == "audio":
                            audio_bytes = base64.b64decode(data.get("data", ""))
                            await audio_queue.put(audio_bytes)
                        elif data.get("type") == "video":
                            video_bytes = base64.b64decode(data.get("data", ""))
                            await video_queue.put({"data": video_bytes, "mode": data.get("mode", "webcam")})
                        elif data.get("type") == "end":
                            stream_logger.info("Client ended turn")
                        elif data.get("type") == "auth":
                            # Late auth — ignore (already created agent)
                            pass
                    except json.JSONDecodeError:
                        stream_logger.error("Bad JSON from client")
                    except Exception as e:
                        stream_logger.error(f"Client message error: {e}")

            async def send_audio_to_service():
                while True:
                    data = await audio_queue.get()
                    live_request_queue.send_realtime(
                        types.Blob(data=data, mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}")
                    )
                    audio_queue.task_done()

            async def send_video_to_service():
                while True:
                    video_data = await video_queue.get()
                    live_request_queue.send_realtime(
                        types.Blob(data=video_data["data"], mime_type="image/jpeg")
                    )
                    video_queue.task_done()

            async def receive_service_responses():
                input_texts = []
                output_texts = []
                current_session_id = None
                interrupted = False

                async for event in runner.run_live(
                    user_id=user_id,
                    session_id=session_id,
                    live_request_queue=live_request_queue,
                    run_config=run_config,
                ):
                    event_str = str(event)

                    if hasattr(event, 'session_resumption_update') and event.session_resumption_update:
                        update = event.session_resumption_update
                        if update.resumable and update.new_handle:
                            current_session_id = update.new_handle
                            stream_logger.info(f"Session handle: {current_session_id}")
                            await websocket.send(json.dumps({"type": "session_id", "data": current_session_id}))

                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if hasattr(part, "inline_data") and part.inline_data:
                                b64_audio = base64.b64encode(part.inline_data.data).decode("utf-8")
                                await websocket.send(json.dumps({"type": "audio", "data": b64_audio}))

                            if hasattr(part, "text") and part.text:
                                if hasattr(event.content, "role") and event.content.role == "user":
                                    if "partial=True" in event_str:
                                        await websocket.send(json.dumps({"type": "user_transcript", "data": part.text}))
                                    input_texts.append(part.text)
                                else:
                                    if "partial=True" in event_str:
                                        await websocket.send(json.dumps({"type": "text", "data": part.text}))
                                        output_texts.append(part.text)

                    if event.interrupted and not interrupted:
                        stream_logger.warning("User interrupted")
                        await websocket.send(json.dumps({"type": "interrupted", "data": "interrupted"}))
                        interrupted = True

                    if event.turn_complete:
                        if not interrupted:
                            stream_logger.info("Turn complete")
                            await websocket.send(json.dumps({"type": "turn_complete", "session_id": current_session_id}))

                        if input_texts:
                            stream_logger.info(f"User said: {' '.join(dict.fromkeys(input_texts))}")
                        if output_texts:
                            stream_logger.info(f"Nemo said: {' '.join(dict.fromkeys(output_texts))}")

                        input_texts = []
                        output_texts = []
                        interrupted = False

            tg.create_task(receive_client_messages(), name="ClientReceiver")
            tg.create_task(send_audio_to_service(), name="AudioSender")
            tg.create_task(send_video_to_service(), name="VideoSender")
            tg.create_task(receive_service_responses(), name="ServiceReceiver")


async def _serve_client_http():
    """Serve the voice client HTML/JS on port 3001 for the Telegram Mini App."""
    from aiohttp import web
    import pathlib
    client_dir = pathlib.Path(__file__).parent.parent / "client"

    async def index(request):
        html = (client_dir / "interface.html").read_text()
        return web.Response(text=html, content_type="text/html")

    async def static(request):
        fname = request.match_info["filename"]
        fpath = client_dir / fname
        if not fpath.exists() or not fpath.is_file():
            return web.Response(status=404)
        return web.Response(
            body=fpath.read_bytes(),
            content_type="application/javascript" if fname.endswith(".js") else "text/plain",
        )

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/{filename}", static)
    runner = web.AppRunner(app)
    await runner.setup()
    http_port = int(os.environ.get("VOICE_HTTP_PORT", "3001"))
    site = web.TCPSite(runner, "0.0.0.0", http_port)
    await site.start()
    stream_logger.info("Voice client HTTP server on port %d", http_port)


async def main():
    server = StreamingService()
    await asyncio.gather(
        server.start_server(),
        _serve_client_http(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        stream_logger.info("Shutting down.")
    except Exception as e:
        stream_logger.critical(f"Fatal error: {e}")
        traceback.print_exc()
