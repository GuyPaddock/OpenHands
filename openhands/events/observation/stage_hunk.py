from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class StageHunkObservation(Observation):
    """Result of a stage_hunk invocation."""

    staged: list[dict] | None = None
    available_hunks: list[dict] | None = None
    reset_performed: bool = False
    thought: str = ''
    observation: str = ObservationType.STAGE_HUNK

    @property
    def message(self) -> str:
        staged_count = len(self.staged or [])
        available_count = len(self.available_hunks or [])
        reset_note = 'after reset' if self.reset_performed else 'with current index'
        return (
            f'Staged {staged_count} selection(s) {reset_note}. '
            f'{available_count} hunks remain available for staging.'
        )

    def __str__(self) -> str:
        return (
            f'[stage_hunk] staged={len(self.staged or [])}, '
            f'available={len(self.available_hunks or [])}, '
            f'reset={self.reset_performed}'
        )

