from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class ApplyPatchObservation(Observation):
    """Result of a successful apply_patch invocation."""

    patch: str | None = None
    patch_id: str | None = None
    applied_hunks: list[dict] | None = None
    thought: str = ''
    observation: str = ObservationType.APPLY_PATCH

    @property
    def message(self) -> str:
        """Get a human-readable message describing the patch operation."""
        return f'I applied the patch:\n{self.patch}.'

    def __str__(self) -> str:
        """Get a string representation of the patch observation."""
        return f'[Patch was applied.]\n{self.patch}'
