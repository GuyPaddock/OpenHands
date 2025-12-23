from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.llm.tool_names import TASK_TRACKER_TOOL_NAME

_DETAILED_TASK_TRACKER_DESCRIPTION = """
This tool provides structured task management capabilities for development workflows.

Use this tool to maintain the task plan in accordance with TASK_MANAGEMENT_POLICY in the system prompt.
Use it to keep tasks, statuses, and progress in sync with the plan you have communicated to the user.

The following sections DEFINE REQUIRED BEHAVIOR when using this tool. They ARE NOT optional.

<TOOL_USAGE_OVERVIEW>
- Use task_tracker for multi-step or multi-phase work, or when the user requests structured planning.
- Do not use task_tracker for trivial or single-step tasks where tracking adds no value.
- If unsure whether the user wants formal task tracking for a small task, ask first.
</TOOL_USAGE_OVERVIEW>

<TOOL_CORE_BEHAVIORS>
Adhere to TASK_MANAGEMENT_POLICY:
- Maintain a structured plan with clear, actionable tasks.
- Only one task should be in_progress at a time.
- Mark tasks done immediately upon full completion.
- Do not silently add or remove tasks; inform the user when the plan changes.
- Preserve plan integrity:
  - The tracker represents the full known plan, not only the next few steps.
  - Do not truncate the list to just short-term actions.
  - Only remove tasks when they are completed, clearly invalid, or explicitly de-scoped with user approval.
  - Prefer refining/updating tasks over replacing the plan wholesale.
- If a task cannot proceed, mark it as blocked, explain why, and ask for clarification.
</TOOL_CORE_BEHAVIORS>

<TOOL_STATUS_SEMANTICS>
- todo: defined but not started.
- in_progress: currently being executed (maintain a single active focus).
- done: fully completed.
- blocked: cannot proceed without input, resources, or resolution
</TOOL_STATUS_SEMANTICS>

<TOOL_RECOMMENDED_WORKFLOW>
1. Before changing the plan, call task_tracker with `command="view"` to see the current tasks and statuses.
2. When planning or updating, use `command="plan"` with a complete task_list that reflects the full known plan.
3. Update task statuses as work progresses (todo → in_progress → done or blocked).
4. After any substantial change to the plan, summarize the updated tasks and statuses to the user.
</TOOL_RECOMMENDED_WORKFLOW>

<TOOL_COUNTER_EXAMPLES>
When NOT to use this tool:
- Single, simple information requests (e.g., "What is the syntax for a for loop in JavaScript?")
- Very small, atomic edits (e.g., "Add a docstring to this one function") where a plan would add overhead.
</TOOL_COUNTER_EXAMPLES>
"""


def create_task_tracker_tool() -> ChatCompletionToolParam:
    return ChatCompletionToolParam(
        type='function',
        function=ChatCompletionToolParamFunctionChunk(
            name=TASK_TRACKER_TOOL_NAME,
            description=_DETAILED_TASK_TRACKER_DESCRIPTION,
            parameters={
                'type': 'object',
                'properties': {
                    'command': {
                        'type': 'string',
                        'enum': ['view', 'plan'],
                        'description': 'The command to execute. `view` shows the current task list. `plan` creates or updates the task list based on provided requirements and progress. Always `view` the current list before making changes.',
                    },
                    'task_list': {
                        'type': 'array',
                        'description': 'The full task list. Required parameter of `plan` command.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'id': {
                                    'type': 'string',
                                    'description': 'Unique task identifier',
                                },
                                'title': {
                                    'type': 'string',
                                    'description': 'Brief task description',
                                },
                                'status': {
                                    'type': 'string',
                                    'description': 'Current task status',
                                    'enum': ['todo', 'in_progress', 'done'],
                                },
                                'notes': {
                                    'type': 'string',
                                    'description': 'Optional additional context or details',
                                },
                            },
                            'required': ['title', 'status', 'id'],
                            'additionalProperties': False,
                        },
                    },
                },
                'required': ['command'],
                'additionalProperties': False,
            },
        ),
    )
