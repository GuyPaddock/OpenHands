from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.agenthub.codeact_agent.tools.security_utils import (
    RISK_LEVELS,
    SECURITY_RISK_DESC,
)

_FILE_EDIT_DESCRIPTION = """Edit files in plain-text format by replacing, modifying, or appending content in
a controlled and predictable way.

This operates under OpenHands workspace safety and integrity rules. Use this tool to apply targeted edits while
minimizing unintended file corruption.

The following sections DEFINE REQUIRED BEHAVIOR when using this tool. They ARE NOT optional.

<TOOL_USAGE_OVERVIEW>
- Provide the file path to edit
- Provide draft replacement content
- Specify the range of lines to edit using start and end (1-indexed, inclusive)
- Use -1 for start or end to refer to the last line of the file
- If the file does not exist, it will be created with the provided content
</TOOL_USAGE_OVERVIEW>

<TOOL_RULES>
- Always include a reason comment at the top of the draft in the format:
  #EDIT: <reason for edit>
- Do not reference or include content outside the specified start/end range
- Keep indentation exact; incorrect whitespace will corrupt code
- The first line of the draft must also be correctly indented
- Keep at least one unchanged line before and after edited content whenever possible
</TOOL_RULES>

<TOOL_RANGE_RULES>
- For large files (approximately > 300 lines), you MUST limit edits to a range smaller than 300 lines
- Always ensure start and end fully cover all original lines referenced in the draft
- Avoid setting start and end to the same value unless appending
- To append, set both start and end to -1
</TOOL_RANGE_RULES>

<TOOL_FORMAT_RULES>
- Draft content does NOT need to include the whole original file
- You may omit unchanged regions using comments such as:
  # ... existing code ...
- However, only use such comments to indicate unchanged sections; do not use them to reference code outside the edit range
</TOOL_FORMAT_RULES>

<TOOL_FAILURE_PREVENTION>
- Incorrect ranges cause corrupted edits; double-check them
- Ensure indentation is correct on every edited line
- Ensure the first draft line is correctly indented
- NEVER refer to text outside the declared edit range
</TOOL_FAILURE_PREVENTION>

<TOOL_EXAMPLES>
<TOOL_EXAMPLE ID="1" DESCRIPTION="General edit for short files">
For example, given an existing file `/path/to/file.py` that looks like this:
(this is the beginning of the file)
1|class MyClass:
2|    def __init__(self):
3|        self.x = 1
4|        self.y = 2
5|        self.z = 3
6|
7|print(MyClass().z)
8|print(MyClass().x)
(this is the end of the file)

The assistant wants to edit the file to look like this:
(this is the beginning of the file)
1|class MyClass:
2|    def __init__(self):
3|        self.x = 1
4|        self.y = 2
5|
6|print(MyClass().y)
(this is the end of the file)

The assistant may produce an edit action like this:
path="/path/to/file.txt" start=1 end=-1
content=```
#EDIT: I want to change the value of y to 2
class MyClass:
    def __init__(self):
        # ... existing code ...
        self.y = 2

print(MyClass().y)
```
</TOOL_EXAMPLE>

<TOOL_EXAMPLE ID="2" DESCRIPTION="Append to file for short files">
For example, given an existing file `/path/to/file.py` that looks like this:
(this is the beginning of the file)
1|class MyClass:
2|    def __init__(self):
3|        self.x = 1
4|        self.y = 2
5|        self.z = 3
6|
7|print(MyClass().z)
8|print(MyClass().x)
(this is the end of the file)

To append the following lines to the file:
```python
#EDIT: I want to print the value of y
print(MyClass().y)
```

The assistant may produce an edit action like this:
path="/path/to/file.txt" start=-1 end=-1
content=```
print(MyClass().y)
```
</TOOL_EXAMPLE>

<TOOL_EXAMPLE ID="3" DESCRIPTION="Edit for long files">
Given an existing file `/path/to/file.py` that looks like this:
(1000 more lines above)
1001|class MyClass:
1002|    def __init__(self):
1003|        self.x = 1
1004|        self.y = 2
1005|        self.z = 3
1006|
1007|print(MyClass().z)
1008|print(MyClass().x)
(2000 more lines below)

The assistant wants to edit the file to look like this:

(1000 more lines above)
1001|class MyClass:
1002|    def __init__(self):
1003|        self.x = 1
1004|        self.y = 2
1005|
1006|print(MyClass().y)
(2000 more lines below)

The assistant may produce an edit action like this:
path="/path/to/file.txt" start=1002 end=1008
content=```
#EDIT: I want to change the value of y to 2
    def __init__(self):
        # no changes before
        self.y = 2
        # self.z is removed

# MyClass().z is removed
print(MyClass().y)
```
</TOOL_EXAMPLE>
</TOOL_EXAMPLES>
"""

LLMBasedFileEditTool = ChatCompletionToolParam(
    type='function',
    function=ChatCompletionToolParamFunctionChunk(
        name='edit_file',
        description=_FILE_EDIT_DESCRIPTION,
        parameters={
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': 'The absolute path to the file to be edited.',
                },
                'content': {
                    'type': 'string',
                    'description': 'A draft of the new content for the file being edited. Note that the assistant may skip unchanged lines.',
                },
                'start': {
                    'type': 'integer',
                    'description': 'The starting line number for the edit (1-indexed, inclusive). Default is 1.',
                },
                'end': {
                    'type': 'integer',
                    'description': 'The ending line number for the edit (1-indexed, inclusive). Default is -1 (end of file).',
                },
                'security_risk': {
                    'type': 'string',
                    'description': SECURITY_RISK_DESC,
                    'enum': RISK_LEVELS,
                },
            },
            'required': ['path', 'content', 'security_risk'],
        },
    ),
)
