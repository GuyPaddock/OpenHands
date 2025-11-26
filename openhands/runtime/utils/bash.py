import os
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


class BashSession:
    """High-level orchestration for running bash commands inside tmux.

    This class is intentionally small and delegates to helpers:

    * :mod:`shell.tmux_driver` – raw tmux / pane IO
    * :mod:`shell.prompt_detector` – PS1 / metadata parsing
    * :mod:`shell.output_parser` – extracting command output

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
            username: Reserved for future multi-user support; if set and
                environment flags allow, the shell will be started via
                ``su <username> -``.
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
    # Utility helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_boolean(value: str) -> bool:
        """Interpret typical environment-style boolean strings.

        Accepted truthy values (case-insensitive):
            "1", "true", "t", "yes", "y", "on"

        Args:
            value: Raw string value (e.g. from os.getenv).

        Returns:
            True if the value is considered truthy, otherwise False.
        """
        return value.lower() in ("1", "true", "t", "yes", "y", "on")

    @staticmethod
    def _is_special_key(command: str) -> bool:
        """Check if the command is a special key, of the form C-<key>."""
        return ((stripped_command := command.strip()) and
                stripped_command.startswith('C-') and
                len(stripped_command) == 3
                and stripped_command[2].isalpha())

    def _maybe_block_new_command(
        self,
        action: CmdRunAction,
        command: str,
    ) -> Optional[CmdOutputObservation]:
        """Return a blocking observation if a previous command is still running.

        Preserves original behavior:

        - Only blocks when the session state is RUNNING
        - Only blocks for non-input actions (action.is_input is False)
        - Returns the current pane snapshot and a descriptive suffix
        """
        if self.state.state is not RunState.RUNNING or action.is_input:
            return None

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

    def _prepare_command_for_execution(
        self,
        command: str,
        action: CmdRunAction,
    ) -> str:
        """Append completion sentinel for full commands and update state.

        Semantics:
        - Interactive input (is_input=True) is sent as-is.
        - Non-input commands get a unique stderr sentinel appended.
        - Sets SessionState.state to RUNNING for non-input commands.
        - Stores pending_sentinel so the fallback path can detect it.

        Args:
            command: Original command string.
            action: CmdRunAction describing the request.

        Returns:
            The actual string that should be sent to the shell.
        """
        to_send = command

        if not action.is_input:
            sentinel = f"__OH_DONE__{uuid.uuid4()}"
            self.state.pending_sentinel = sentinel
            # Print sentinel to stderr so stdout pipelines are minimally affected.
            to_send = f'{command}; printf "{sentinel}" >&2'
            self.state.state = RunState.RUNNING

        return to_send

    def _capture_pane_or_error(self) -> str | ErrorObservation:
        """Capture pane contents or return an ErrorObservation on failure.

        Preserves original behavior:
        - On tmux capture failure, transition to IDLE and clear sentinel.
        - On success, updates last_output with the full pane text.

        Returns:
            Captured pane text, or ErrorObservation if capture failed.
        """
        try:
            pane: str = self.tmux.capture()
        except Exception as exc:  # tmux/session failure
            self.state.state = RunState.IDLE
            self.state.pending_sentinel = None
            return ErrorObservation(
                content=f"Bash session became unresponsive. Error: {exc}"
            )

        # Track last output for potential higher-level diagnostics.
        self.state.last_output = pane
        return pane

    def _finalize_completed(
        self,
        command: str,
        pane: str,
        prompt_match,
    ) -> CmdOutputObservation:
        """Run completion handler and update state like the original loop."""
        obs = self._handle_completed(command, pane, prompt_match)
        self.state.state = RunState.COMPLETED
        self.state.pending_sentinel = None
        return obs

    def _finalize_no_output(
        self,
        command: str,
        pane: str,
    ) -> CmdOutputObservation:
        """Run no-output handler and update state like the original loop."""
        obs = self._handle_no_output(command, pane)
        self.state.state = RunState.NO_OUTPUT_TIMEOUT
        self.state.pending_sentinel = None
        return obs

    def _finalize_hard_timeout(
        self,
        command: str,
        pane: str,
        timeout: float,
    ) -> CmdOutputObservation:
        """Run hard-timeout handler and update state like the original loop."""
        obs = self._handle_hard_timeout(command, pane, timeout)
        self.state.state = RunState.HARD_TIMEOUT
        self.state.pending_sentinel = None
        return obs

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
        """Start tmux + bash and configure a deterministic prompt.

        If `username` is set and SU_TO_USER / RUNTIME_USERNAME conditions
        are satisfied, we will launch a login shell using `su <username> -`.
        """
        # Base shell command
        shell_cmd = "/bin/bash"

        # Optional user-switch, controlled by environment variables:
        #
        #   RUNTIME_USERNAME  – runtime's current username
        #   SU_TO_USER        – if "true"/"1"/"yes"/etc, allow su behavior
        #
        # Only specific usernames are allowed: the runtime user, "root",
        # or "openhands".
        if self.username is not None:
            su_to_user = self._parse_boolean(os.getenv("SU_TO_USER", "true"))
            runtime_username = os.getenv("RUNTIME_USERNAME")

            if su_to_user and self.username in filter(
                None,
                (runtime_username, "root", "openhands"),
            ):
                # Launch a login shell for the requested user.
                shell_cmd = f"su {self.username} -"

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

        # Block new commands while the previous is running.
        blocked = self._maybe_block_new_command(action, command)
        if blocked is not None:
            return blocked

        # --------------------------------------------------------------
        # SPECIAL-KEY PATH (C-c/C-d/C-z) FOR INTERACTIVE INPUT
        # --------------------------------------------------------------
        if action.is_input and self._is_special_key(command):
            # Send the actual control keystroke; tmux interprets "C-c" as Ctrl-C.
            self.tmux.send_keys(command, enter=False)
            time.sleep(self.POLL_INTERVAL)

            # Synthesize a completion, even if no prompt/sentinel is visible.
            pane = self.tmux.capture()
            meta = CmdOutputMetadata()
            meta.suffix = (
                f"[CTRL-{command[-1].upper()} sent. "
                "The running command was interrupted.]"
            )

            # Reset session state so new commands are allowed.
            self.state.state = RunState.COMPLETED
            self.state.pending_sentinel = None

            # Treat the entire pane as content; higher layers can decide
            # how to surface this.
            return CmdOutputObservation(
                content=pane.rstrip(),
                command=command,
                metadata=meta,
            )

        # --------------------------------------------------------------
        # NORMAL PATH (non-special commands)
        # --------------------------------------------------------------
        run_uuid: Optional[str] = self.state.last_prompt_uuid
        start: float = time.time()
        last_change: float = start
        initial_output: str = self.tmux.capture()

        # Append completion sentinel for full commands and update state.
        to_send: str = self._prepare_command_for_execution(command, action)

        # Actually send the command / input to the pane.
        self.tmux.send_keys(to_send, enter=not action.is_input)

        # Main polling loop
        while should_continue():
            pane_or_error = self._capture_pane_or_error()
            if isinstance(pane_or_error, ErrorObservation):
                # tmux/session failure, already reset state
                return pane_or_error

            pane: str = pane_or_error
            now: float = time.time()

            # ------------------------------------------------------------------
            # Completion via PS1 prompt (primary path)
            # ------------------------------------------------------------------
            new_prompt = prompt_detector.new_prompt_after(run_uuid, pane)
            prompts = prompt_detector.find_prompts(pane)

            if new_prompt is not None:
                return self._finalize_completed(command, pane, new_prompt)

            # ------------------------------------------------------------------
            # Completion via sentinel fallback (if the UUID heuristics fail)
            # ------------------------------------------------------------------
            if (
                self.state.pending_sentinel is not None
                and self.state.pending_sentinel in pane
                and prompts
            ):
                fallback_prompt = prompts[-1]
                return self._finalize_completed(command, pane, fallback_prompt)

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
                return self._finalize_no_output(command, pane)

            # ------------------------------------------------------------------
            # Hard timeout regardless of blocking mode
            # ------------------------------------------------------------------
            if action.timeout and (now - start) > float(action.timeout):
                return self._finalize_hard_timeout(
                    command, pane, float(action.timeout)
                )

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
