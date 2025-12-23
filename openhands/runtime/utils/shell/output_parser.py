import re
from typing import List

from . import prompt_detector


def remove_command_prefix(command_output: str, command: str) -> str:
    """Strip the echoed command from the output."""
    return command_output.lstrip().removeprefix(command.lstrip()).lstrip()


def get_active_pane_content(pane: str) -> str:
    """Extract the content after the last prompt in the pane."""
    prompts = prompt_detector.find_prompts(pane)

    # If we have prompts, return everything after the *last* prompt.
    # FIX: Use end() directly, do not add +1
    return pane[prompts[-1].end():] if prompts else pane


def extract_output_using_prompt_match(
    pane: str,
    prompts: List[re.Match],
    prompt_match: re.Match,
) -> str:
    """
    Extracts and stitches together command output segments between prompts.

    This iterates through all found prompts in the pane. For each prompt
    preceding the `prompt_match`, it extracts the text immediately following
    it up to the start of the next prompt (or the `prompt_match` itself).

    Args:
        pane: The raw text content of the tmux pane.
        prompts: A list of all prompt matches found in the pane.
        prompt_match: The specific prompt match indicating the command's completion.

    Returns:
        str: The consolidated output text found between the prompts.
    """
    segments: List[str] = []

    for index, prompt in enumerate(prompts):
        if prompt == prompt_match:
            break

        # Determine the end of the current segment:
        # If there is a next prompt in the list, stop there.
        # Otherwise (edge case), stop at the target prompt_match start.
        end_prompt = prompts[index + 1] if (index + 1 < len(prompts)) else prompt_match
        segments.append(pane[prompt.end(): end_prompt.start()])

    return "\n".join(segments)
