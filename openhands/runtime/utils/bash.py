import os
import re
import time
from typing import Optional

from openhands.events.action import CmdRunAction
from openhands.events.observation import CmdOutputObservation, ErrorObservation
from openhands.events.observation.commands import CmdOutputMetadata, CMD_OUTPUT_PS1_END
from openhands.runtime.utils.bash_constants import TIMEOUT_MESSAGE_TEMPLATE
from openhands.utils.shutdown_listener import should_continue

from .shell.session_state import SessionState, RunState
from .shell.tmux_driver import TmuxDriver
from .shell import prompt_detector
from .shell import output_parser
from .shell import input_parser


RESET_SESSION_COMMAND = "__reset_bash_session__"


class BashSession:
    """High-level orchestration for running bash commands inside tmux.

    This class is intentionally small and delegates to helpers:

    * :mod:`shell.tmux_driver` – raw tmux / pane IO
    * :mod:`shell.prompt_detector` – PS1 / metadata parsing
    * :mod:`shell.output_parser` – extracting command output
    * :mod:`shell.input_parser` – command parsing and sanitization

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

    def _maybe_block_new_command(
        self,
        action: CmdRunAction,
        command: str,
    ) -> Optional[CmdOutputObservation]:
        """Return a blocking observation if a previous command is still running.

        Preserves original behavior:

        - Only blocks when the session state indicates a command is still running
        - Only blocks for non-input actions (action.is_input is False)
        - Returns the current pane snapshot and a descriptive suffix
        """
        if self.state.state not in (
            RunState.RUNNING,
            RunState.NO_OUTPUT_TIMEOUT,
            RunState.HARD_TIMEOUT,
        ) or action.is_input:
            return None

        meta = CmdOutputMetadata()
        meta.suffix = (
            f'\n[Your command "{command}" is NOT executed. '
            "The previous command is still running - You CANNOT send new "
            "commands until the previous command is completed. "
            "By setting `is_input` to `true`, you can interact with the "
            "current process or send Ctrl+C to stop it. "
            "If the shell is stuck, run the reset command "
            f"`{RESET_SESSION_COMMAND}` to restart it: {TIMEOUT_MESSAGE_TEMPLATE}]"
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
        """Preprocess a command before it's sent to tmux.

        Args:
            command: Original command string.
            action: CmdRunAction describing the request.

        Returns:
            The actual string that should be sent to the shell.
        """
        to_send = command

        if not action.is_input:
            # IMPORTANT: Escape special chars before sending to tmux.
            to_send = input_parser.escape_bash_special_chars(to_send)
            self.state.state = RunState.RUNNING
            self.state.last_command = command

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
        return obs

    def _finalize_no_output(self, command: str, pane: str) -> CmdOutputObservation:
        """Run no-output handler and update state like the original loop."""
        obs = self._handle_no_output(command, pane)
        self.state.state = RunState.RUNNING
        return obs

    def _finalize_hard_timeout(self, command: str, pane: str,
                               timeout: float) -> CmdOutputObservation:
        """Run hard-timeout handler and update state like the original loop."""
        obs = self._handle_hard_timeout(command, pane, timeout)
        self.state.state = RunState.HARD_TIMEOUT
        return obs

    def _poll_running_command_output(self) -> CmdOutputObservation | ErrorObservation:
        """Poll the current pane to surface new output for a running command."""

        pane_or_error = self._capture_pane_or_error()
        if isinstance(pane_or_error, ErrorObservation):
            return pane_or_error

        pane: str = pane_or_error
        prompts = prompt_detector.find_prompts(pane)
        last_command = self.state.last_command

        # If a prompt is now visible, the previous command has completed.
        if prompts:
            return self._finalize_completed(last_command, pane, prompts[-1])

        # Otherwise, return the active pane content while keeping the session in
        # a running state.
        active_pane_content = output_parser.get_active_pane_content(pane)
        if last_command:
            active_pane_content = output_parser.remove_command_prefix(
                active_pane_content, last_command
            )

        return CmdOutputObservation(
            content=active_pane_content.rstrip(),
            command=last_command,
            metadata=CmdOutputMetadata(),
        )

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
        """Start tmux and bash and configure a deterministic prompt.

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
            su_to_user = input_parser.parse_boolean(os.getenv("SU_TO_USER", "true"))
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

    def reset(self) -> None:
        """Reset the tmux session and clear tracked state."""

        # Tear down any existing session first.
        self.close()

        # Reset local state trackers to defaults before restarting.
        self.state = SessionState()

        # Restart the tmux/bash session.
        self.initialize()

    def close(self) -> None:
        """Terminate tmux session."""
        if self.tmux is not None:
            self.tmux.kill()
            self.tmux = None

        self.state.state = RunState.IDLE

    def _handle_reset_request(self) -> CmdOutputObservation | ErrorObservation:
        """Reset the session on demand and return a confirmation observation."""

        try:
            self.reset()
        except Exception as exc:  # pragma: no cover - defensive logging path
            return ErrorObservation(
                content=(
                    "Failed to reset the bash session. "
                    f"Try again or restart the runtime. Details: {exc}"
                )
            )

        meta = CmdOutputMetadata()
        meta.suffix = "[Bash session has been reset. You can run new commands.]"

        return CmdOutputObservation(
            content="",
            command=RESET_SESSION_COMMAND,
            metadata=meta,
        )

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
        command: str = action.command.strip()

        if command == RESET_SESSION_COMMAND:
            return self._handle_reset_request()

        if self.tmux is None:
            return ErrorObservation(content="Bash session is not initialized.")

        if not command:
            # Empty non-input commands attempt to re-fetch output/logs.
            if not action.is_input:
                return CmdOutputObservation(
                    content="ERROR: No previous running command to retrieve logs from.",
                    command="",
                    metadata=CmdOutputMetadata(),
                )

            # For interactive input, treat an empty command as a request to poll
            # the currently running process for more output.
            if self.state.state in (
                RunState.RUNNING,
                RunState.NO_OUTPUT_TIMEOUT,
                RunState.HARD_TIMEOUT,
            ):
                return self._poll_running_command_output()

            return CmdOutputObservation(
                content="ERROR: No previous running command to interact with.",
                command="",
                metadata=CmdOutputMetadata(),
            )

        # Block new commands while the previous is running.
        blocked = self._maybe_block_new_command(action, command)
        if blocked is not None:
            return blocked

        # Check for multiple commands using bashlex
        split_cmds = input_parser.split_bash_commands(command)
        if len(split_cmds) > 1:
            return ErrorObservation(
                content=(
                    f'ERROR: Cannot execute multiple commands at once.\n'
                    f'Please run each command separately OR chain them into a single command via && or ;\n'
                    f'Provided commands:\n{"\n".join(f"({i + 1}) {cmd}" for i, cmd in enumerate(split_cmds))}'
                )
            )

        # Special Key Path (C-c/C-d/C-z) for interactive commands
        if action.is_input and input_parser.is_special_key(command):
            # Send the actual control keystroke; tmux interprets "C-c" as Ctrl-C.
            self.tmux.send_keys(command, enter=False)
            time.sleep(self.POLL_INTERVAL)

            # Synthesize a completion, even if no prompt/sentinel is visible.
            pane = self.tmux.capture()

            # Strip the PS1 prompt from the special key output
            active_pane_content = output_parser.get_active_pane_content(pane)

            meta = CmdOutputMetadata()
            meta.suffix = (
                f"[CTRL-{command[-1].upper()} sent. "
                "The running command was interrupted.]"
            )

            # Reset session state so new commands are allowed.
            self.state.state = RunState.COMPLETED

            return CmdOutputObservation(
                content=active_pane_content.rstrip(),
                command=command,
                metadata=meta,
            )

        # --------------------------------------------------------------
        # NORMAL PATH (non-special commands)
        # --------------------------------------------------------------
        start: float = time.time()
        last_change: float = start

        # Snapshot state BEFORE sending command
        initial_output: str = self.tmux.capture()
        initial_prompts = prompt_detector.find_prompts(initial_output)
        initial_prompt_count = len(initial_prompts)

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

            current_prompts = prompt_detector.find_prompts(pane)
            current_prompt_count = len(current_prompts)

            # COMPLETION CHECK 1: The number of prompts increased.
            # This is the standard signal that a command finished.
            if current_prompt_count > initial_prompt_count:
                return self._finalize_completed(command, pane, current_prompts[-1])

            # COMPLETION CHECK 2: The output ends with the PS1 marker.
            # This handles cases where text scrolled off the top (so count didn't increase)
            # but we are definitely sitting at a prompt.
            if pane.rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip()):
                # If we have any prompts visible, the last one is the active one
                if current_prompts:
                    return self._finalize_completed(command, pane, current_prompts[-1])

            # Timeout Logic
            if pane != initial_output:
                last_change = now
                initial_output = pane

            if (
                not action.blocking
                and (now - last_change) > float(self.no_change_timeout)
            ):
                return self._finalize_no_output(command, pane)

            # Hard timeout regardless of blocking mode
            if action.timeout and (now - start) > float(action.timeout):
                return self._finalize_hard_timeout(
                    command, pane, float(action.timeout)
                )

            time.sleep(self.POLL_INTERVAL)

        # Shutdown listener requested stop.
        self.state.state = RunState.IDLE

        return ErrorObservation(content="Session interrupted.")

    # ------------------------------------------------------------------ #
    # Result handlers
    # ------------------------------------------------------------------ #

    def _handle_completed(
        self,
        command: str,
        pane: str,
        prompt_match: re.Match[str],
    ) -> CmdOutputObservation:
        """
        Handle the completion of a command using the *specific* prompt_match
        that triggered completion.

        Args:
            command: The executed command string.
            pane: Full captured pane text at completion time.
            prompt_match: The precise PS1 metadata prompt that marks completion.

        Returns:
            CmdOutputObservation: An observation object containing cleaned
            command output and extracted metadata.
        """

        # Build metadata from the specific prompt.
        meta: CmdOutputMetadata = CmdOutputMetadata.from_ps1_match(prompt_match)

        # Store the current working directory.
        if getattr(meta, "working_dir", None):
            self.state.cwd = meta.working_dir

        # Find all prompts for context.
        prompts: list[re.Match[str]] = prompt_detector.find_prompts(pane)

        # Check truncation: only this prompt is visible.
        only_prompt_visible: bool = (len(prompts) == 1 and prompts[0] == prompt_match)

        if only_prompt_visible:
            raw_output: str = pane[: prompt_match.start()]
            num_lines: int = len(raw_output.splitlines())

            if num_lines > 0:
                meta.prefix = (
                    "[Previous command outputs are truncated. "
                    f"Showing the last {num_lines} lines of the output below.]\n"
                )

        else:
            # Multi-prompt case: stitch output between prompts using prompt_match
            # as the final anchor.
            raw_output = output_parser.extract_output_using_prompt_match(
                pane,
                prompts,
                prompt_match,
            )

        # Remove the echoed command from the output
        cleaned_output = output_parser.remove_command_prefix(raw_output, command)

        # Apply exit-code and special key suffixes.
        self._apply_special_suffixes(command, meta)

        return self._finalize_successful_completion(
            command,
            cleaned_output,
            meta,
        )

    def _handle_no_output(self, command: str, pane: str) -> CmdOutputObservation:
        # Strip the prompt and the echoed command for timeouts.
        active_pane_content = output_parser.get_active_pane_content(pane)
        trimmed_content = output_parser.remove_command_prefix(active_pane_content, command)

        meta = CmdOutputMetadata()
        meta.awaiting_input = True
        meta.suffix = (
            "[The process is still running and awaiting input. "
            "Send follow-up input with `is_input=true` (e.g., responses to prompts or "
            "`C-c`/`C-d`/`C-z`). If the shell is stuck, run the reset command "
            f"`{RESET_SESSION_COMMAND}`: {TIMEOUT_MESSAGE_TEMPLATE}]"
        )
        return CmdOutputObservation(content=trimmed_content, command=command, metadata=meta)

    def _handle_hard_timeout(self, command: str, pane: str, timeout: float) -> CmdOutputObservation:
        # Strip the prompt and the echoed command for timeouts.
        active_pane_content = output_parser.get_active_pane_content(pane)
        trimmed_content = output_parser.remove_command_prefix(active_pane_content, command)

        meta = CmdOutputMetadata()
        meta.awaiting_input = False
        meta.suffix = f"[Command timed out after {timeout} seconds. {TIMEOUT_MESSAGE_TEMPLATE}]"
        return CmdOutputObservation(content=trimmed_content, command=command, metadata=meta)

    def _apply_special_suffixes(self, command: str, meta: CmdOutputMetadata):
        is_special_key = input_parser.is_special_key(command)

        if hasattr(meta, "exit_code"):
            if is_special_key:
                meta.suffix = (
                    f"\n[The command completed with exit code {meta.exit_code}. "
                    f"CTRL+{command[-1].upper()} was sent.]"
                )
            else:
                meta.suffix = f"\n[The command completed with exit code {meta.exit_code}.]"

    def _finalize_successful_completion(
        self,
        command: str,
        output: str,
        meta: CmdOutputMetadata,
    ) -> CmdOutputObservation:
        """Clear terminal scrollback and build the final observation."""
        if self.tmux is not None:
            self.tmux.clear()

        self.state.state = RunState.COMPLETED

        # A command that completed should no longer be treated as awaiting input.
        meta.awaiting_input = False

        return CmdOutputObservation(
            content=output.rstrip(),
            command=command,
            metadata=meta,
        )
