import time
import uuid
from typing import Optional

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
    """High-level orchestration for running bash commands inside tmux.

    This class is intentionally small and delegates to helpers:

    * :mod:`shell.tmux_driver` – raw tmux / pane IO
    * :mod:`shell.prompt_detector` – PS1 / metadata parsing
    * :mod:`shell.output_parser` – extracting command output
    * :mod:`shell.zombie_guard` – best-effort cleanup of defunct tmux processes

    It exposes a single public method, :meth:`execute`, which takes a
    :class:`CmdRunAction` and returns either :class:`CmdOutputObservation`
    or :class:`ErrorObservation`.
    """

    HISTORY_LIMIT: int = 10_000
    POLL_INTERVAL: float = 0.4

    def __init__(
        self,
        work_dir: str,
        username: Optional[str] = None,
        no_change_timeout_seconds: int = 30,
        **_: object,
    ) -> None:
        """Create an uninitialized bash session wrapper.

        The tmux session is not started until :meth:`initialize` is called.

        Args:
            work_dir: Filesystem path where the shell will start.
            username: Reserved for future multi-user support (currently unused).
            no_change_timeout_seconds: Time without new output before a
                "no-output" timeout is considered for non-blocking actions.
            **_: Ignored extra keyword arguments for forward-compatibility.
        """
        self.work_dir: str = work_dir
        self.username: Optional[str] = username
        self.no_change_timeout: int = no_change_timeout_seconds

        self.state: SessionState = SessionState()
        self.tmux: Optional[TmuxDriver] = None

    # ------------------------------------------------------------------ #
    # Lifecycle helpers
    # ------------------------------------------------------------------ #

    @property
    def cwd(self) -> Optional[str]:
        """Return the last working directory reported by the shell.

        This is populated from the PS1 metadata exposed by
        :class:`CmdOutputMetadata` and updated whenever a command completes.
        """
        return self.state.cwd

    def initialize(self) -> None:
        """Start tmux + bash and configure a deterministic prompt."""
        zombie_guard.cleanup()

        shell_cmd = "/bin/bash"
        self.tmux = TmuxDriver(self.work_dir, shell_cmd, self.HISTORY_LIMIT)
        self.tmux.start()

        ps1 = CmdOutputMetadata.to_ps1_prompt()
        self.tmux.configure_prompt(ps1)
        self.tmux.clear()

        self.state.state = RunState.IDLE
        self.state.cwd = self.work_dir
        self.state.last_prompt_uuid = None
        self.state.pending_sentinel = None

    def close(self) -> None:
        """Terminate tmux session and attempt zombie cleanup."""
        if self.tmux is not None:
            self.tmux.kill()
            self.tmux = None

        zombie_guard.cleanup()
        self.state.state = RunState.IDLE
        self.state.pending_sentinel = None

    # ------------------------------------------------------------------ #
    # Main API
    # ------------------------------------------------------------------ #

    def execute(
        self,
        action: CmdRunAction,
    ) -> CmdOutputObservation | ErrorObservation:
        """Execute a bash command or send interactive input.

          * Tracks the working directory via PS1 metadata.
          * Uses a completion sentinel printed to stderr as a *fallback* when
            prompt-based completion is ambiguous.
          * Blocks new non-input commands while a previous command is still
            considered running, returning an explanatory message instead of
            queuing additional shell commands.

        Args:
            action: The command invocation description supplied by OpenHands.

        Returns:
            A :class:`CmdOutputObservation` on success/timeout, or an
            :class:`ErrorObservation` if the underlying shell/session
            became unusable.
        """
        if self.tmux is None:
            return ErrorObservation(content="Bash session is not initialized.")

        command: str = action.command.strip()

        # Bare "input"/empty commands are not meaningful in this design.
        if not command:
            return CmdOutputObservation(
                content="ERROR: No previous running command.",
                command="",
                metadata=CmdOutputMetadata(),
            )

        # Block new commands while the previous is running
        if self.state.state is RunState.RUNNING and not action.is_input:
            meta = CmdOutputMetadata()
            meta.suffix = (
                f'\n[Your command "{command}" is NOT executed. '
                "The previous command is still running - You CANNOT send new "
                "commands until the previous command is completed. "
                "By setting `is_input` to `true`, you can interact with the "
                f"current process: {TIMEOUT_MESSAGE_TEMPLATE}]"
            )
            pane_snapshot = self.tmux.capture()
            return CmdOutputObservation(
                content=pane_snapshot,
                command=command,
                metadata=meta,
            )

        run_uuid: Optional[str] = self.state.last_prompt_uuid
        start: float = time.time()
        last_change: float = start
        initial_output: str = self.tmux.capture()

        # Append a completion sentinel for full commands.
        to_send: str = command

        if not action.is_input:
            sentinel = f"__OH_DONE__{uuid.uuid4()}"
            self.state.pending_sentinel = sentinel
            # Print sentinel to stderr so stdout pipelines are minimally affected.
            to_send = f'{command}; printf "{sentinel}" >&2'
            self.state.state = RunState.RUNNING

        # Actually send the command / input to the pane.
        self.tmux.send_keys(to_send, enter=not action.is_input)

        # Main polling loop
        while should_continue():
            try:
                pane: str = self.tmux.capture()
            except Exception as exc:  # tmux/session failure
                self.state.state = RunState.IDLE
                self.state.pending_sentinel = None
                return ErrorObservation(
                    content=f"Bash session became unresponsive. Error: {exc}"
                )

            now: float = time.time()

            # Track last output for potential higher-level diagnostics.
            self.state.last_output = pane

            # ------------------------------------------------------------------
            # Completion via PS1 prompt (primary path)
            # ------------------------------------------------------------------
            new_prompt = prompt_detector.new_prompt_after(run_uuid, pane)
            prompts = prompt_detector.find_prompts(pane)

            if new_prompt is not None:
                obs = self._handle_completed(command, pane, new_prompt)
                self.state.state = RunState.COMPLETED
                self.state.pending_sentinel = None
                return obs

            # ------------------------------------------------------------------
            # Completion via sentinel fallback (if the UUID heuristics fail)
            # ------------------------------------------------------------------
            if (
                self.state.pending_sentinel is not None
                and self.state.pending_sentinel in pane
                and prompts
            ):
                fallback_prompt = prompts[-1]
                obs = self._handle_completed(command, pane, fallback_prompt)
                self.state.state = RunState.COMPLETED
                self.state.pending_sentinel = None
                return obs

            # ------------------------------------------------------------------
            # No-output timeout for non-blocking actions
            # ------------------------------------------------------------------
            if pane != initial_output:
                last_change = now
                initial_output = pane

            if (
                not action.blocking
                and (now - last_change) > float(self.no_change_timeout)
            ):
                obs = self._handle_no_output(command, pane)
                self.state.state = RunState.NO_OUTPUT_TIMEOUT
                self.state.pending_sentinel = None
                return obs

            # ------------------------------------------------------------------
            # Hard timeout regardless of blocking mode
            # ------------------------------------------------------------------
            if action.timeout and (now - start) > float(action.timeout):
                obs = self._handle_hard_timeout(command, pane, float(action.timeout))
                self.state.state = RunState.HARD_TIMEOUT
                self.state.pending_sentinel = None
                return obs

            time.sleep(self.POLL_INTERVAL)

        # Shutdown listener requested stop.
        self.state.state = RunState.IDLE
        self.state.pending_sentinel = None
        return ErrorObservation(content="Session interrupted.")

    # ------------------------------------------------------------------ #
    # Result handlers
    # ------------------------------------------------------------------ #

    def _handle_completed(
        self,
        command: str,
        pane: str,
        prompt_match,
    ) -> CmdOutputObservation:
        """Build a completion observation once a new prompt is detected.

        This method:

        1. Re-parses all prompts in the pane.
        2. Uses :class:`CmdOutputMetadata` to reconstruct metadata.
        3. Extracts output between prompts via :mod:`output_parser`.
        4. Updates session state (UUID and working directory).
        """
        prompts = prompt_detector.find_prompts(pane)
        meta = CmdOutputMetadata.from_ps1_match(prompt_match)
        out = output_parser.extract_between_prompts(pane, prompts)

        # Working-directory tracking
        self.state.last_prompt_uuid = meta.uuid
        if getattr(meta, "working_dir", None):
            self.state.cwd = meta.working_dir

        return CmdOutputObservation(
            content=out.rstrip(),
            command=command,
            metadata=meta,
        )

    @staticmethod
    def _handle_no_output(
        command: str,
        pane: str,
    ) -> CmdOutputObservation:
        """Return an observation for a no-output timeout."""
        meta = CmdOutputMetadata()
        meta.suffix = f"[No output for timeout. {TIMEOUT_MESSAGE_TEMPLATE}]"
        return CmdOutputObservation(
            content=pane,
            command=command,
            metadata=meta,
        )

    @staticmethod
    def _handle_hard_timeout(
        command: str,
        pane: str,
        timeout: float,
    ) -> CmdOutputObservation:
        """Return an observation for a hard timeout."""
        meta = CmdOutputMetadata()
        meta.suffix = (
            f"[Command timed out after {timeout} seconds. "
            f"{TIMEOUT_MESSAGE_TEMPLATE}]"
        )
        return CmdOutputObservation(
            content=pane,
            command=command,
            metadata=meta,
        )
