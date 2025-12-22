from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.agenthub.codeact_agent.tools.security_utils import (
    RISK_LEVELS,
    SECURITY_RISK_DESC,
)
from openhands.llm.tool_names import VIEW_FILE_TOOL_NAME

_VIEW_FILE_DESCRIPTION = """Read files with line numbers or list directory contents.
- Paths may be absolute or relative to the workspace root (/workspace).
- Files are displayed with `cat -n` semantics and support optional line ranges via `view_range`.
- Directories list non-hidden entries up to 2 levels deep.
- Large outputs may be truncated and marked with <response clipped>."""


def create_view_file_tool() -> ChatCompletionToolParam:
    return ChatCompletionToolParam(
        type="function",
        function=ChatCompletionToolParamFunctionChunk(
            name=VIEW_FILE_TOOL_NAME,
            description=_VIEW_FILE_DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file or directory to inspect.",
                    },
                    "view_range": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Optional [start, end] line numbers (1-indexed) when reading a file.",
                    },
                    "security_risk": {
                        "type": "string",
                        "description": SECURITY_RISK_DESC,
                        "enum": RISK_LEVELS,
                    },
                },
                "required": ["path", "security_risk"],
            },
        ),
    )
