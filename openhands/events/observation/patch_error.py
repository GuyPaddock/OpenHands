from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class PatchErrorObservation(Observation):
    """Result of a failed apply_patch invocation."""

    observation: str = ObservationType.PATCH_ERROR
    error_id: str = ''

    @property
    def message(self) -> str:
        return self.content
