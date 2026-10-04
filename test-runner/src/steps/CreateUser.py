"""POST /api/create-user"""

from __future__ import annotations

from typing import TYPE_CHECKING

from steps.Step import Step, StepRequest

if TYPE_CHECKING:
    from model.Group import Group
    from model.User import User


class CreateUser(Step):
    """Create `user` on the server. Does not modify the model."""

    def __init__(self, user: User) -> None:
        self.user = user

    def build_request(self, group: Group) -> StepRequest:
        return StepRequest("POST", "/api/create-user", json={
            "login": self.user.login,
            "password": self.user.password,
            "email": self.user.email,
        })
