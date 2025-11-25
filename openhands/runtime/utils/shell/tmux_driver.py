import time
import libtmux

class TmuxDriver:
    """Thin wrapper around tmux. No prompt logic."""

    def __init__(self, work_dir: str, shell_cmd: str, history_limit: int = 10000):
        self.work_dir = work_dir
        self.shell_cmd = shell_cmd
        self.history_limit = history_limit

        self.server = None
        self.session = None
        self.window = None
        self.pane = None

    def start(self):
        self.server = libtmux.Server()
        # Responsive check
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

    def configure_prompt(self, ps1):
        self.pane.send_keys(
            f'export PROMPT_COMMAND=\'export PS1="{ps1}"\'; export PS2=""'
        )
        time.sleep(0.1)

    def send_keys(self, s: str, enter: bool = True):
        self.pane.send_keys(s, enter=enter)

    def capture(self) -> str:
        lines = self.pane.cmd("capture-pane", "-J", "-pS", "-").stdout
        return "\n".join(line.rstrip() for line in lines)

    def clear(self):
        self.pane.send_keys("C-l", enter=False)
        time.sleep(0.1)
        self.pane.cmd("clear-history")

    def kill(self):
        if self.session:
            try:
                self.session.kill()
            except Exception:
                pass
        if self.server:
            try:
                if not self.server.sessions:
                    self.server.kill()
            except Exception:
                try:
                    self.server.kill()
                except Exception:
                    pass
