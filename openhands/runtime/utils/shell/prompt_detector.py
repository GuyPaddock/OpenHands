import re
from openhands.events.observation.commands import CmdOutputMetadata


def find_prompts(pane_output: str) -> list[re.Match]:
    """Detect a new PS1 prompt (new UUID) in captured pane output."""
    return CmdOutputMetadata.matches_ps1_metadata(pane_output)

def new_prompt_after(run_uuid: Optional[str], pane_output: str):
    """Return a match for the next unique UUID after the run_uuid."""
    matches = CmdOutputMetadata.matches_ps1_metadata(pane_output)
    for m in matches:
        meta = CmdOutputMetadata.from_ps1_match(m)
        if run_uuid is None or meta.uuid != run_uuid:
            return m
    return None
