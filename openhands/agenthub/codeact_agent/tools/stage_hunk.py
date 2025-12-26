from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.agenthub.codeact_agent.tools.security_utils import (
    RISK_LEVELS,
    SECURITY_RISK_DESC,
)
from openhands.llm.tool_names import STAGE_HUNK_TOOL_NAME

_STAGE_HUNK_DESCRIPTION = """Interactively stage code changes in the git index without running shell commands.

Usage pattern:
- First call this tool to receive the list of available hunks and line-level options.
- Call the tool again with a "selections" payload that references the provided hunk IDs and line numbers.
- Set "reset_index" to true when you need to clear the index before staging.

Notes:
- Behaves like `git add -p`, allowing you to stage entire hunks or individual lines.
- Paths must be absolute (starting with /) when specifying files in selections.
- If there are no unstaged changes, the tool will respond with an empty list of available hunks.
"""


def create_stage_hunk_tool() -> ChatCompletionToolParam:
    return ChatCompletionToolParam(
        type="function",
        function=ChatCompletionToolParamFunctionChunk(
            name=STAGE_HUNK_TOOL_NAME,
            description=_STAGE_HUNK_DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "reset_index": {
                        "type": "boolean",
                        "description": "If true, run `git reset` before staging selections.",
                        "default": False,
                    },
                    "selections": {
                        "type": "array",
                        "description": (
                            "List of staging selections. Each entry references a hunk ID returned by"
                            " a previous call and may optionally limit staging to specific line numbers"
                            " within that hunk."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "file": {
                                    "type": "string",
                                    "description": "Absolute path to the file that owns the hunk.",
                                },
                                "hunk_id": {
                                    "type": "string",
                                    "description": "Identifier of the hunk to stage, provided by the tool response.",
                                },
                                "include_lines": {
                                    "type": "array",
                                    "description": (
                                        "Optional 1-based line indexes within the hunk to stage."
                                        " If omitted, the entire hunk is staged."
                                    ),
                                    "items": {"type": "integer", "minimum": 1},
                                },
                            },
                            "required": ["file", "hunk_id"],
                        },
                    },
                    "security_risk": {
                        "type": "string",
                        "description": SECURITY_RISK_DESC,
                        "enum": RISK_LEVELS,
                    },
                },
                "required": ["security_risk"],
            },
        ),
    )

