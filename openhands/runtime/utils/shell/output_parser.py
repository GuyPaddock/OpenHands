import re
from typing import List


def extract_between_prompts(
    pane_output: str,
    prompts: List[re.Match],
) -> str:
    """Extract the command output that appears between prompts.

    The typical layout of a pane is::

        PS1(metadata)\n
        <command echo + output>\n
        PS1(metadata)\n

    This helper stitches together all text that lies *after* each prompt
    and *before* the next prompt. If there are no prompts, the entire
    pane is returned.
    """
    if not prompts:
        return pane_output

    if len(prompts) == 1:
        # Single prompt – everything after it is considered command output.
        return pane_output[prompts[0].end() + 1 :]

    segments: List[str] = []

    for i in range(len(prompts) - 1):
        seg = pane_output[prompts[i].end() + 1 : prompts[i + 1].start()]
        segments.append(seg)

    # Include anything after the last prompt as well.
    segments.append(pane_output[prompts[-1].end() + 1 :])
    return "\n".join(segments)
