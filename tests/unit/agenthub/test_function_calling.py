"""Test function calling module."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from litellm import ModelResponse

from openhands.agenthub.codeact_agent.function_calling import response_to_actions
from openhands.core.exceptions import FunctionCallValidationError
from openhands.events.action import (
    BrowseInteractiveAction,
    CmdRunAction,
    FileEditAction,
    FileReadAction,
    IPythonRunCellAction,
)
from openhands.events.event import FileEditSource


def create_mock_response(function_name: str, arguments: dict) -> ModelResponse:
    """Helper function to create a mock response with a tool call."""
    return ModelResponse(
        id='mock-id',
        choices=[
            {
                'message': {
                    'tool_calls': [
                        {
                            'function': {
                                'name': function_name,
                                'arguments': json.dumps(arguments),
                            },
                            'id': 'mock-tool-call-id',
                            'type': 'function',
                        }
                    ],
                    'content': None,
                    'role': 'assistant',
                },
                'index': 0,
                'finish_reason': 'tool_calls',
            }
        ],
    )


def test_execute_bash_valid():
    """Test execute_bash with valid arguments."""
    response = create_mock_response(
        'execute_bash', {'command': 'ls', 'is_input': 'false', 'security_risk': 'LOW'}
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], CmdRunAction)
    assert actions[0].command == 'ls'
    assert actions[0].is_input is False

    # Test with timeout parameter
    with patch.object(CmdRunAction, 'set_hard_timeout') as mock_set_hard_timeout:
        response_with_timeout = create_mock_response(
            'execute_bash',
            {
                'command': 'ls',
                'is_input': 'false',
                'timeout': 30,
                'security_risk': 'LOW',
            },
        )
        actions_with_timeout = response_to_actions(response_with_timeout)

        # Verify set_hard_timeout was called with the correct value
        mock_set_hard_timeout.assert_called_once_with(30.0)

        assert len(actions_with_timeout) == 1
        assert isinstance(actions_with_timeout[0], CmdRunAction)
        assert actions_with_timeout[0].command == 'ls'
        assert actions_with_timeout[0].is_input is False


def test_execute_bash_missing_command():
    """Test execute_bash with missing command argument."""
    response = create_mock_response(
        'execute_bash', {'is_input': 'false', 'security_risk': 'LOW'}
    )
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "command"' in str(exc_info.value)


def test_execute_ipython_cell_valid():
    """Test execute_ipython_cell with valid arguments."""
    response = create_mock_response(
        'execute_ipython_cell', {'code': "print('hello')", 'security_risk': 'LOW'}
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], IPythonRunCellAction)
    assert actions[0].code == "print('hello')"


def test_execute_ipython_cell_missing_code():
    """Test execute_ipython_cell with missing code argument."""
    response = create_mock_response('execute_ipython_cell', {'security_risk': 'LOW'})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "code"' in str(exc_info.value)


def test_edit_file_valid():
    """Test edit_file with valid arguments."""
    response = create_mock_response(
        'edit_file',
        {
            'path': '/path/to/file',
            'content': 'file content',
            'start': 1,
            'end': 10,
            'security_risk': 'LOW',
        },
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], FileEditAction)
    assert actions[0].path == '/path/to/file'
    assert actions[0].content == 'file content'
    assert actions[0].start == 1
    assert actions[0].end == 10


def test_edit_file_missing_required():
    """Test edit_file with missing required arguments."""
    # Missing path
    response = create_mock_response(
        'edit_file', {'content': 'content', 'security_risk': 'LOW'}
    )
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "path"' in str(exc_info.value)

    # Missing content
    response = create_mock_response(
        'edit_file', {'path': '/path/to/file', 'security_risk': 'LOW'}
    )
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "content"' in str(exc_info.value)
def test_apply_patch_valid():
    """Test apply_patch command construction."""
    patch_text = """*** Begin Patch
*** Patch-ID: demo
*** Add File: foo.txt
hello
*** End Patch"""
    response = create_mock_response(
        'apply_patch', {'patch': patch_text, 'security_risk': 'LOW'}
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], CmdRunAction)
    repo_root = Path(__file__).resolve().parents[3].as_posix()
    expected_prefix = (
        f'PYTHONPATH="{repo_root}" APPLY_PATCH_JSON=1 python -m openhands.utils.apply_patch <<\'PATCH\''
    )
    assert expected_prefix in actions[0].command
    assert patch_text.rstrip('\n') in actions[0].command


def test_apply_patch_missing_required():
    """Test apply_patch with missing patch payload."""
    response = create_mock_response('apply_patch', {'security_risk': 'LOW'})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "patch"' in str(exc_info.value)


def test_view_file_valid():
    """Test view_file mapping to FileReadAction."""
    response = create_mock_response(
        'view_file', {'path': '/workspace/foo.txt', 'security_risk': 'LOW'}
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], FileReadAction)
    assert actions[0].path == '/workspace/foo.txt'
    assert actions[0].view_range is None


def test_view_file_with_range():
    """Test view_file supports view_range argument."""
    response = create_mock_response(
        'view_file',
        {'path': '/workspace/foo.txt', 'view_range': [5, 10], 'security_risk': 'LOW'},
    )
    actions = response_to_actions(response)
    assert isinstance(actions[0], FileReadAction)
    assert actions[0].view_range == [5, 10]


def test_browser_valid():
    """Test browser with valid arguments."""
    response = create_mock_response(
        'browser', {'code': "click('button-1')", 'security_risk': 'LOW'}
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], BrowseInteractiveAction)
    assert actions[0].browser_actions == "click('button-1')"
    assert actions[0].return_axtree is False  # Default value should be False


def test_browser_missing_code():
    """Test browser with missing code argument."""
    response = create_mock_response('browser', {'security_risk': 'LOW'})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "code"' in str(exc_info.value)


def test_invalid_json_arguments():
    """Test handling of invalid JSON in arguments."""
    response = ModelResponse(
        id='mock-id',
        choices=[
            {
                'message': {
                    'tool_calls': [
                        {
                            'function': {
                                'name': 'execute_bash',
                                'arguments': 'invalid json',
                            },
                            'id': 'mock-tool-call-id',
                            'type': 'function',
                        }
                    ],
                    'content': None,
                    'role': 'assistant',
                },
                'index': 0,
                'finish_reason': 'tool_calls',
            }
        ],
    )
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Failed to parse tool call arguments' in str(exc_info.value)


