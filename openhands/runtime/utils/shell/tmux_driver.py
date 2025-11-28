import time
from typing import Optional

import libtmux

from openhands.core.logger import openhands_logger as logger


class TmuxDriver:
    """Thin wrapper around tmux.

    This layer is intentionally dumb: it knows how to start a tmux
    session, create a window/pane, capture its contents and tear it
    down again. It does *not* know anything about prompts or shell
    semantics.
    """

    def __init__(
        self,
        work_dir: str,
        shell_cmd: str,
        history_limit: int = 10_000,
    ) -> None:
        """Construct an unstarted driver.

        Args:
            work_dir: Directory to use as the tmux session's starting CWD.
            shell_cmd: Command used to start the interactive shell
                (typically ``/bin/bash``).
            history_limit: tmux scrollback history limit.
        """
        self.work_dir: str = work_dir
        self.shell_cmd: str = shell_cmd
        self.history_limit: int = history_limit

        self.server: Optional[libtmux.Server] = None
        self.session: Optional[libtmux.Session] = None
        self.window: Optional[libtmux.Window] = None
        self.pane: Optional[libtmux.Pane] = None

        self._session_name: Optional[str] = None
        self._current_ps1: Optional[str] = None

    def __del__(self) -> None:
        """Ensure the tmux session is cleaned up when the object is destroyed."""
        self.kill()

    def start(self) -> None:
        """Create and start Bash running in a new tmux server, session, window, and pane.

        tmux is configured to remain running even if Bash dies (e.g., due to set -e).
        """
        self.server = libtmux.Server()

        # Touch sessions to ensure the server is responsive.
        _ = self.server.sessions

        self._session_name = f"openhands-{time.time_ns()}"
        self.session = self.server.new_session(
            session_name=self._session_name,
            start_directory=self.work_dir,
            kill_session=True,
            x=1000,
            y=1000,
        )

        # Set tmux history limit globally.
        self.session.set_option("history-limit", str(self.history_limit), global_=True)

        # tmux always creates a default initial window during new_session. Let's use it as the
        # keepalive window.
        keepalive_window = self.session.active_window
        assert keepalive_window is not None

        keepalive_window.rename_window("keepalive")
        keepalive_pane = keepalive_window.active_pane

        # Replace whatever shell tmux auto-launched with a persistent keepalive process.
        keepalive_pane.send_keys("exec tail -f /dev/null", enter=True)
        time.sleep(0.05)

        # Initialize the bash window that OpenHands will interact with.
        self._initialize_bash_pane()

    def configure_prompt(self, ps1: str) -> None:
        """Install a deterministic PS1/PS2 for easier prompt detection."""
        self._current_ps1 = ps1

        # Only configure PS1 if bash is alive. Otherwise, it will be configured automatically the
        # next time bash is respawned.
        if self._is_bash_alive():
            assert self.pane is not None
            self.pane.send_keys(
                f"export PROMPT_COMMAND='export PS1=\"{ps1}\"'; export PS2=\"\""
            )
            # Wait for the command to take effect.
            time.sleep(0.1)

    def send_keys(self, s: str, *, enter: bool = True) -> None:
        """Send raw keystrokes to the active pane."""
        self._ensure_bash_alive()

        assert self.pane is not None
        self.pane.send_keys(s, enter=enter)

    def capture(self) -> str:
        """Capture the full contents of the active pane as a single string."""
        self._ensure_bash_alive()

        assert self.pane is not None
        lines = self.pane.cmd("capture-pane", "-J", "-pS", "-").stdout
        return "\n".join(line.rstrip() for line in lines)

    def clear(self) -> None:
        """Clear on-screen content and pane scrollback."""
        self._ensure_bash_alive()

        assert self.pane is not None
        self.pane.send_keys("C-l", enter=False)
        time.sleep(0.1)
        self.pane.cmd("clear-history")

    def kill(self) -> None:
        """Tear down the tmux session and, if appropriate, the server.

        The method is deliberately best-effort; any exceptions from tmux
        are swallowed so that higher-level teardown can proceed.
        """
        if self.session is not None:
            try:
                self.session.kill()
            except Exception:
                pass
            finally:
                self.session = None

        if self.server is not None:
            try:
                # If there are no remaining sessions, kill the server.
                if not self.server.sessions:
                    self.server.kill()
            except Exception:
                try:
                    self.server.kill()
                except Exception:
                    pass
            finally:
                self.server = None

    def _ensure_bash_alive(self) -> None:
        """Ensure the Bash pane is alive.

        If the pane, the window, or the session disappears (e.g., bash exited due to `set -e`),
        automatically respawn it while keeping the keepalive window intact."""
        if not self._is_bash_alive():
            logger.error(
                f'The Bash pane in tmux session "{self._session_name}" exited and is being '
                f'respawned.'
            )
            self._initialize_bash_pane()

    def _is_bash_alive(self) -> bool:
        """Check whether the Bash pane is alive and able to be captured.

        If the pane, the window, or the session disappears (e.g., bash exited due to `set -e`),
        Bash is no longer considered alive."""
        is_alive = True

        try:
            # Validate session and window association
            if self.session is None or self.window is None:
                logger.warning(
                    f'The Bash session or window in tmux session "{self._session_name}" is missing.'
                )

                is_alive = False
            else:
                # Refresh objects from libtmux (prevents stale references)
                self.session.refresh()
                self.window.refresh()

                # Validate pane existence.
                if self.pane not in self.window.panes:
                    logger.warning(
                        f'The Bash window in tmux session "{self._session_name}" has exited.'
                    )

                    is_alive = False
                else:
                    # Try a trivial capture to ensure the pane is responsive
                    try:
                        _ = self.pane.capture_pane()
                    except Exception:
                        logger.warning(
                            f'The Bash window in tmux session "{self._session_name}" is '
                            f'unresponsive.'
                        )
                        is_alive = False

        except Exception as e:
            logger.warning(
                f'Encountered an error when checking on the health of the Bash window in tmux '
                f'session "{self._session_name}": {e}'
            )
            is_alive = False

        return is_alive

    def _initialize_bash_pane(self) -> None:
        # Create a REAL bash window for OpenHands to interact with.
        bash_window = self._create_bash_window()

        # Select the Bash pane for actual use.
        self.window = bash_window
        self.pane = bash_window.active_pane

        if self._current_ps1 is not None:
            # Reconfigure the prompt to match the last one we received.
            self.configure_prompt(self._current_ps1)

    def _create_bash_window(self) -> libtmux.Window:
        """Create and return a new Bash window."""
        window = self.session.new_window(
            window_name="bash",
            window_shell=self.shell_cmd,
            start_directory=self.work_dir,
            attach=False
        )

        return window
