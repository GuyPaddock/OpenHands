from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.success import SuccessObservation


@dataclass
class PatchSuccessObservation(SuccessObservation):
    """Result of a successful apply_patch invocation."""

    observation: str = ObservationType.PATCH_SUCCESS
