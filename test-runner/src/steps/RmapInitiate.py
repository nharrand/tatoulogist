"""POST /api/rmap-initiate"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from steps.Step import Step, StepRequest

if TYPE_CHECKING:
    from model.Group import Group


class RmapInitiate(Step):
    """Send an already encrypted RMAP message 1: {"payload": "<base64>"}.

    Building and decrypting the messages is the scenario's job (with
    rmap.RMAPClient); this step only sends it.
    """

    def __init__(self, message: dict[str, Any]) -> None:
        self.message = message

    def build_request(self, group: Group) -> StepRequest:
        return StepRequest("POST", "/api/rmap-initiate", json=self.message)
