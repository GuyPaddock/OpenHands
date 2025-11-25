from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RunState(Enum):
    """High-level lifecycle status of the most recent command."""

    IDLE = 1
    RUNNING = 2
    COMPLETED = 3
    NO_OUTPUT_TIMEOUT = 4
    HARD_TIMEOUT = 5


@dataclass
class SessionState:
    """Pure state container for :class:`BashSession`.

    This object deliberately contains *no* IO or tmux logic. It is
    used purely to track shell-level state that survives across calls
    to :meth:`BashSession.execute`.
    """

    state: RunState = RunState.IDLE
    #: UUID of the last PS1 prompt that completed a command.
    last_prompt_uuid: Optional[str] = None
    #: Last full pane capture (for debugging or higher-level use).
    last_output: str = ""
    #: Last working directory reported by the prompt metadata.
    cwd: Optional[str] = None
    #: Sentinel string appended to the current command, if any.
    pending_sentinel: Optional[str] = None
