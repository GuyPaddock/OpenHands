import time
from typing import Optional

import libtmux


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

    def start(self) -> None:
        """Create a new tmux server/session/window/pane."""
        self.server = libtmux.Server()
        # Touch sessions to ensure the server is responsive.
        _ = self.server.sessions

        session_name = f"openhands-{time.time_ns()}"
        self.session = self.server.new_session(
            session_name=session_name,
            start_directory=self.work_dir,
            kill_session=True,
            x=1000,
            y=1000,
        )

        init_win = self.session.active_window
        self.window = self.session.new_window(
            window_name="bash",
            window_shell=self.shell_cmd,
            start_directory=self.work_dir,
        )
        self.pane = self.window.active_pane
        self.session.set_option("history-limit", str(self.history_limit), global_=True)
        init_win.kill()

    def configure_prompt(self, ps1: str) -> None:
        """Install a deterministic PS1/PS2 for easier prompt detection."""
        assert self.pane is not None
        self.pane.send_keys(
            f"export PROMPT_COMMAND='export PS1=\"{ps1}\"'; export PS2=\"\""
        )
        # Wait for command to take effect
        time.sleep(0.1)

    def send_keys(self, s: str, *, enter: bool = True) -> None:
        """Send raw keystrokes to the active pane."""
        assert self.pane is not None
        self.pane.send_keys(s, enter=enter)

    def capture(self) -> str:
        """Capture the full contents of the active pane as a single string."""
        assert self.pane is not None
        lines = self.pane.cmd("capture-pane", "-J", "-pS", "-").stdout
        return "\n".join(line.rstrip() for line in lines)

    def clear(self) -> None:
        """Clear on-screen content and pane scrollback."""
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
