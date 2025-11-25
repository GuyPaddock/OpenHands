from enum import Enum

class RunState(Enum):
    IDLE = 1
    RUNNING = 2
    COMPLETED = 3
    NO_OUTPUT_TIMEOUT = 4
    HARD_TIMEOUT = 5

class SessionState:
    """Pure state machine for command lifecycle."""

    def __init__(self):
        self.state = RunState.IDLE
        self.last_prompt_uuid = None
        self.last_output = ""
        self.cwd = None
