from unittest.mock import Mock

from openhands.events.action import CmdRunAction
from openhands.runtime.utils.bash import (
    BashSession,
    RESET_SESSION_COMMAND,
)
from openhands.runtime.utils.shell.session_state import RunState


def _session_with_mock_tmux(tmp_path):
    session = BashSession(work_dir=str(tmp_path))
    session.tmux = Mock()
    session.tmux.capture.return_value = "pane"
    return session


def test_blocking_after_timeout_states(tmp_path):
    session = _session_with_mock_tmux(tmp_path)
    action = CmdRunAction("ls")

    for state in (
        RunState.RUNNING,
        RunState.NO_OUTPUT_TIMEOUT,
        RunState.HARD_TIMEOUT,
    ):
        session.state.state = state
        blocked = session._maybe_block_new_command(action, action.command)
        assert blocked is not None
        assert "NOT executed" in blocked.metadata.suffix


def test_reset_request_resets_session(tmp_path, monkeypatch):
    session = BashSession(work_dir=str(tmp_path))
    session.tmux = Mock()
    session.tmux.kill = Mock()

    initialized = False

    def fake_initialize():
        nonlocal initialized
        initialized = True
        session.tmux = Mock()
        session.tmux.capture.return_value = ""

    monkeypatch.setattr(session, "initialize", fake_initialize)

    obs = session.execute(CmdRunAction(RESET_SESSION_COMMAND))

    session.tmux.kill.assert_called_once()
    assert initialized
    assert obs.command == RESET_SESSION_COMMAND
    assert "reset" in obs.metadata.suffix.lower()
    assert session.state.state == RunState.IDLE
