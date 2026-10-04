"""GET /api/get-version/<link>"""

from __future__ import annotations

from typing import TYPE_CHECKING

from steps.Step import Step, StepRequest

if TYPE_CHECKING:
    from model.Group import Group
    from model.User import User


class GetVersion(Step):
    """Download the watermarked PDF behind `link` (in result.content).

    The endpoint is public, so no token is sent unless `user` is given.
    """

    def __init__(self, link: str, user: User | None = None) -> None:
        self.link = link
        self.user = user

    def build_request(self, group: Group) -> StepRequest:
        return StepRequest("GET", f"/api/get-version/{self.link}", user=self.user)
