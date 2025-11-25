import time

from openhands.events.action import CmdRunAction
from openhands.events.observation import CmdOutputObservation, ErrorObservation
from openhands.events.observation.commands import CmdOutputMetadata
from openhands.runtime.utils.bash_constants import TIMEOUT_MESSAGE_TEMPLATE
from openhands.utils.shutdown_listener import should_continue

from .shell.session_state import SessionState, RunState
from .shell.tmux_driver import TmuxDriver
from .shell import prompt_detector
from .shell import output_parser
from .shell import zombie_guard

class BashSession:
    HISTORY_LIMIT = 10_000
    POLL_INTERVAL = 0.4

    def __init__(self, work_dir, username=None, no_change_timeout_seconds=30, **_):
        self.work_dir = work_dir
        self.username = username
        self.no_change_timeout = no_change_timeout_seconds

        self.state = SessionState()
        self.tmux = None

    def initialize(self):
        zombie_guard.cleanup()

        shell_cmd = "/bin/bash"
        self.tmux = TmuxDriver(self.work_dir, shell_cmd, self.HISTORY_LIMIT)
        self.tmux.start()

        ps1 = CmdOutputMetadata.to_ps1_prompt()
        self.tmux.configure_prompt(ps1)
        self.tmux.clear()

        self.state.state = RunState.IDLE

    def close(self):
        if self.tmux:
            self.tmux.kill()
        zombie_guard.cleanup()

    def execute(self, action: CmdRunAction):
        """Main entrypoint."""
        command = action.command.strip()
        if not command:
            return CmdOutputObservation(
                content="ERROR: No previous running command.",
                command="",
                metadata=CmdOutputMetadata(),
            )

        # Start command
        run_uuid = self.state.last_prompt_uuid
        self.tmux.send_keys(command, enter=not action.is_input)

        start = time.time()
        last_change = time.time()
        initial_output = self.tmux.capture()

        while should_continue():
            pane = self.tmux.capture()

            # Check completion
            new_prompt = prompt_detector.new_prompt_after(run_uuid, pane)
            if new_prompt:
                return self._handle_completed(command, pane, new_prompt)

            # No output timeout
            if not action.blocking and time.time() - last_change > self.no_change_timeout:
                return self._handle_no_output(command, pane)

            # Hard timeout
            if action.timeout and (time.time() - start) > action.timeout:
                return self._handle_hard_timeout(command, pane, action.timeout)

            if pane != initial_output:
                last_change = time.time()
                initial_output = pane

            time.sleep(self.POLL_INTERVAL)

        return ErrorObservation(content="Session interrupted.")

    # ----------------------------------------------
    # Result handlers
    # ----------------------------------------------

    def _handle_completed(self, command, pane, prompt):
        prompts = prompt_detector.find_prompts(pane)
        meta = CmdOutputMetadata.from_ps1_match(prompt)
        out = output_parser.extract_between_prompts(pane, prompts)

        self.state.last_prompt_uuid = meta.uuid

        return CmdOutputObservation(
            content=out.rstrip(),
            command=command,
            metadata=meta,
        )

    @staticmethod
    def _handle_no_output(command, pane):
        meta = CmdOutputMetadata()
        meta.suffix = f"[No output for timeout. {TIMEOUT_MESSAGE_TEMPLATE}]"
        return CmdOutputObservation(
            content=pane,
            command=command,
            metadata=meta,
        )

    @staticmethod
    def _handle_hard_timeout(command, pane, timeout):
        meta = CmdOutputMetadata()
        meta.suffix = (
            f"[Command timed out after {timeout} seconds. {TIMEOUT_MESSAGE_TEMPLATE}]"
        )
        return CmdOutputObservation(
            content=pane,
            command=command,
            metadata=meta,
        )
