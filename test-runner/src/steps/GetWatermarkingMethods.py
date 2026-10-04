"""GET /api/get-watermarking-methods"""

from __future__ import annotations

from typing import TYPE_CHECKING

from steps.Step import Step, StepRequest

if TYPE_CHECKING:
    from model.Group import Group
    from model.User import User


class GetWatermarkingMethods(Step):
    """List the available watermarking methods. Authentication is optional."""

    def __init__(self, user: User | None = None) -> None:
        self.user = user

    def build_request(self, group: Group) -> StepRequest:
        return StepRequest("GET", "/api/get-watermarking-methods", user=self.user)
