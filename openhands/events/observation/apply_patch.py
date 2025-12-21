from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class ApplyPatchObservation(Observation):
    """Result of a successful apply_patch invocation."""

    observation: str = ObservationType.APPLY_PATCH

    @property
    def message(self) -> str:
        return self.content
