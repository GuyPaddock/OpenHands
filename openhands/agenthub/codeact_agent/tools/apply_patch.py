from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.agenthub.codeact_agent.tools.security_utils import (
    RISK_LEVELS,
    SECURITY_RISK_DESC,
)
from openhands.llm.tool_names import APPLY_PATCH_TOOL_NAME

_APPLY_PATCH_DESCRIPTION = """Atomic, Codex-style patch tool for creating, updating, and deleting files.

Example:
```
*** Begin Patch
*** Update File: HelloWorld.java
@@
 public static void main(String[] args) {
+    System.out.println("Hello, world!");
 }
*** End Patch
```

Usage:
- This tool can be used for creating and editing files in plain-text format.
- Supports `Add File`, `Update File`, and `Delete File` operations.
- `Add File` cannot be used if the specified `path` already exists as a file.
- State is persistent across command calls and discussions with the user.
- Provide the full patch payload between `*** Begin Patch` and `*** End Patch` lines.
- Optionally include `*** Patch-ID: <id>` for retry correlation.
- Always use absolute file paths (starting with /).

Before using this tool:
1. View the file to understand the file's contents and context.
2. Verify the directory path is correct.

When making edits:
- Ensure the edit results in idiomatic, correct code.
- Do not leave the code in a broken state.
- If making multiple edits in a row to the same file, prefer to send all edits in a single message with multiple
  calls to this tool, rather than multiple messages with a single call each.
- Be mindful of whitespace!

CRITICAL REQUIREMENTS:
1. EXACT MATCHING: The patch context must match EXACTLY one or more consecutive lines from the file, including all
   whitespace and indentation. The tool will fail if the context matches multiple locations or doesn't match exactly
   with the file content.
2. UNIQUENESS: The patch must uniquely identify a single instance in the file:
   - Include sufficient context before and after the change point (3-5 lines recommended).
   - If not unique, the replacement will not be performed.
"""

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
