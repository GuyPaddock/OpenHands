from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.agenthub.codeact_agent.tools.security_utils import (
    RISK_LEVELS,
    SECURITY_RISK_DESC,
)
from openhands.llm.tool_names import APPLY_PATCH_TOOL_NAME

_APPLY_PATCH_DESCRIPTION = """Atomic, Codex-style patch tool for creating, updating, and deleting files.

Usage:
- Provide the full patch payload between `*** Begin Patch` and `*** End Patch` lines.
- Optionally include `*** Patch-ID: <id>` for retry correlation.
- Supports ADD, UPDATE, and DELETE operations with unified diagnostics.
- Emits structured JSON when available (preferred)."""


def create_apply_patch_tool() -> ChatCompletionToolParam:
    return ChatCompletionToolParam(
        type="function",
        function=ChatCompletionToolParamFunctionChunk(
            name=APPLY_PATCH_TOOL_NAME,
            description=_APPLY_PATCH_DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "description": (
                            "Full patch content including the *** Begin Patch/End Patch markers."
                        ),
                    },
                    "security_risk": {
                        "type": "string",
                        "description": SECURITY_RISK_DESC,
                        "enum": RISK_LEVELS,
                    },
                },
                "required": ["patch", "security_risk"],
            },
        ),
    )
