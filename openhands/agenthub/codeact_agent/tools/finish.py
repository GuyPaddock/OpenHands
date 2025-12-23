from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.llm.tool_names import FINISH_TOOL_NAME

_FINISH_DESCRIPTION = """Signal the completion of the current task or conversation.

The following sections DEFINE REQUIRED BEHAVIOR when using this tool. They ARE NOT optional.

<TOOL_WHEN_TO_USE>
Use this tool when:
- You have successfully completed the user's requested task
- You cannot proceed further due to technical limitations or missing information
</TOOL_WHEN_TO_USE>

<TOOL_RULES>
The message should include:
- A clear summary of actions taken and their results
- Any next steps for the user
- Explanation if you're unable to complete the task
- Any follow-up questions if more information is needed
</TOOL_RULES>
"""

FinishTool = ChatCompletionToolParam(
    type='function',
    function=ChatCompletionToolParamFunctionChunk(
        name=FINISH_TOOL_NAME,
        description=_FINISH_DESCRIPTION,
        parameters={
            'type': 'object',
            'required': ['message'],
            'properties': {
                'message': {
                    'type': 'string',
                    'description': 'Final message to send to the user',
                },
            },
        },
    ),
)
