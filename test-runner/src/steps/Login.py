"""POST /api/login"""

from __future__ import annotations

from typing import TYPE_CHECKING

from steps.Step import Step, StepRequest, StepResult

if TYPE_CHECKING:
    from model.Group import Group
    from model.User import User


class Login(Step):
    """Log `user` in and, on success, cache the returned token on it.

    `email` and `password` override the user's own credentials, which
    negative scenarios can use to try a wrong password. The token is cached
    only when the user's own credentials were used.
    """

    def __init__(self, user: User, *, email: str | None = None,
                 password: str | None = None) -> None:
        self.user = user
        self.email = email
        self.password = password

    def build_request(self, group: Group) -> StepRequest:
        return StepRequest("POST", "/api/login", json={
            "email": self.email if self.email is not None else self.user.email,
            "password": self.password if self.password is not None else self.user.password,
        })

    def after_response(self, group: Group, result: StepResult) -> None:
        if self.email is not None or self.password is not None or not result.ok:
            return
        data = result.response_json
        if isinstance(data, dict) and isinstance(data.get("token"), str):
            expires_in = data.get("expires_in")
            self.user.set_token(data["token"],
                                expires_in if isinstance(expires_in, (int, float)) else None)
