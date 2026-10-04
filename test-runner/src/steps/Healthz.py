"""GET /healthz"""

from __future__ import annotations

from typing import TYPE_CHECKING

from steps.Step import Step, StepRequest

if TYPE_CHECKING:
    from model.Group import Group


class Healthz(Step):
    """Check that the server is up. Never authenticated."""

    def build_request(self, group: Group) -> StepRequest:
        return StepRequest("GET", "/healthz")
