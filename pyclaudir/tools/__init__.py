"""Tool plugin package — drop a new file here, subclass BaseTool, and you're done.

The MCP server auto-discovers every ``BaseTool`` subclass found in modules in
this package at startup. No registry edits required.
"""

from .fetch_url import FetchUrlTool
from .phone_action import PhoneActionTool
from .run_code import RunCodeTool
from .search_memories import SearchMemoriesTool
from .send_voice_message import SendVoiceMessageTool
from .synthesize_memory import SynthesizeMemoryWikiTool

__all__ = [
    "FetchUrlTool",
    "PhoneActionTool",
    "RunCodeTool",
    "SearchMemoriesTool",
    "SendVoiceMessageTool",
    "SynthesizeMemoryWikiTool",
]
