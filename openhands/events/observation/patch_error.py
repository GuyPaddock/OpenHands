from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.error import ErrorObservation


@dataclass
class PatchErrorObservation(ErrorObservation):
    """Result of a failed apply_patch invocation."""

    observation: str = ObservationType.PATCH_ERROR
