"""GET /api/list-all-versions"""

from __future__ import annotations

from typing import TYPE_CHECKING

from steps.Step import Step, StepRequest

if TYPE_CHECKING:
    from model.Group import Group
    from model.User import User


class ListAllVersions(Step):
    """List all versions of all documents of `user`, authenticated with its cached token."""

    def __init__(self, user: User | None) -> None:
        self.user = user

    def build_request(self, group: Group) -> StepRequest:
        return StepRequest("GET", "/api/list-all-versions", user=self.user)
