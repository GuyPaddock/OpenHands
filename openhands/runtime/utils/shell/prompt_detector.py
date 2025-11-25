import re
from re import Match
from typing import List, Optional

from openhands.events.observation.commands import CmdOutputMetadata


def find_prompts(pane_output: str) -> List[re.Match]:
    """Return all PS1 prompt matches present in the captured pane output."""
    return CmdOutputMetadata.matches_ps1_metadata(pane_output)


def new_prompt_after(
    run_uuid: Optional[str],
    pane_output: str,
) -> Optional[Match[str]]:
    """Return the first prompt whose UUID differs from ``run_uuid``.

    Args:
        run_uuid: UUID of the prompt that was active before the current
            command was issued. ``None`` means *any* prompt is acceptable.
        pane_output: Full string capture of the tmux pane.

    Returns:
        A regular-expression match object for the newly detected prompt,
        or ``None`` if no new prompt can be identified yet.
    """
    matches = CmdOutputMetadata.matches_ps1_metadata(pane_output)

    for match in matches:
        meta = CmdOutputMetadata.from_ps1_match(match)

        if run_uuid is None or meta.uuid != run_uuid:
            return match

    return None
