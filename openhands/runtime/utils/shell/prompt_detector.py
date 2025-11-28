import re
from re import Match
from typing import List, Optional

from openhands.events.observation.commands import CmdOutputMetadata


def find_prompts(pane_output: str) -> List[re.Match]:
    """Return all PS1 prompt matches present in the captured pane output."""
    return CmdOutputMetadata.matches_ps1_metadata(pane_output)


def detect_new_prompt(
    initial_output: str,
    current_output: str,
) -> Optional[Match[str]]:
    """Return a prompt if a new one has appeared compared to the initial state.

    This replaces the UUID-based logic which caused crashes.
    """
    initial_matches = find_prompts(initial_output)
    current_matches = find_prompts(current_output)

    # If we have more prompts than before, the last one is likely the new one
    if len(current_matches) > len(initial_matches):
        return current_matches[-1]

    return None
