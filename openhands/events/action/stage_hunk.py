from dataclasses import dataclass
from typing import ClassVar

from openhands.core.schema import ActionType
from openhands.events.action.action import Action, ActionSecurityRisk


@dataclass
class StageHunkSelection:
    file: str
    hunk_id: str
    include_lines: list[int] | None = None


@dataclass
class StageHunkAction(Action):
    """Stage hunks or specific lines into the git index without running bash commands."""

    reset_index: bool = False
    selections: list[StageHunkSelection] | None = None
    thought: str = ''
    action: str = ActionType.STAGE_HUNK
    runnable: ClassVar[bool] = True
    security_risk: ActionSecurityRisk = ActionSecurityRisk.UNKNOWN

    def __repr__(self) -> str:
        selection_count = len(self.selections or [])
        return (
            "**StageHunkAction**\n"
            f"Reset index: {self.reset_index}\n"
            f"Selections: {selection_count}"
        )

