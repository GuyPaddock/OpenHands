from dataclasses import dataclass
from typing import ClassVar

from openhands.core.schema import ActionType
from openhands.events.action.action import Action, ActionSecurityRisk


@dataclass
class ApplyPatchAction(Action):
    """Applies a Codex-style patch across one or more files."""

    patch: str
    thought: str = ''
    action: str = ActionType.APPLY_PATCH
    runnable: ClassVar[bool] = True
    security_risk: ActionSecurityRisk = ActionSecurityRisk.UNKNOWN

    def __repr__(self) -> str:
        return (
            f"**ApplyPatchAction**\n"
            f"Patch:\n```\n{self.patch}\n```\n"
        )
