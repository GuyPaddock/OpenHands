import re
from typing import List

from . import prompt_detector


def remove_command_prefix(command_output: str, command: str) -> str:
    """Strip the echoed command from the output."""
    return command_output.lstrip().removeprefix(command.lstrip()).lstrip()


def get_active_pane_content(pane: str) -> str:
    """Extract the content after the last prompt in the pane.

    This is used for timeouts and interruptions where we want to see what's happened since the
    last prompt (the current execution), but there is no 'new' prompt to mark the end yet.
    """
    prompts = prompt_detector.find_prompts(pane)

    # If we have prompts, return everything after the *last* prompt.
    return pane[prompts[-1].end():] if prompts else pane


def extract_output_using_prompt_match(
    pane: str,
    prompts: List[re.Match],
    prompt_match: re.Match,
) -> str:
    """
    Extract command output using prompt_match as the definitive delimiter.

    The typical layout of a pane is::

        PS1(metadata)\n
        <command echo + output>\n
        PS1(metadata)\n

    This helper stitches together all text that lies *after* each prompt
    and *before* the next prompt.

    Args:
        pane: Entire pane content.
        prompts: All detected prompt matches within the pane.
        prompt_match: The specific prompt signaling command completion.

    Returns:
        A newline-joined string representing the command output.
    """
    segments: List[str] = []

    for i, pr in enumerate(prompts):
        if pr == prompt_match:
            break

        if i + 1 < len(prompts):
            segment: str = pane[pr.end() + 1: prompts[i + 1].start()]
            segments.append(segment)
        else:
            # No next prompt → end at the completion prompt.
            segment = pane[pr.end() + 1: prompt_match.start()]
            segments.append(segment)

    return "\n".join(segments)
