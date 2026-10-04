"""Create a user, log in, and verify the account owns nothing."""

from __future__ import annotations

import secrets
import uuid
from typing import TYPE_CHECKING

from model.User import User
from scenarii.Scenario import Scenario
from steps.CreateUser import CreateUser
from steps.ListAllVersions import ListAllVersions
from steps.ListDocuments import ListDocuments
from steps.Login import Login

if TYPE_CHECKING:
    from model.Group import Group


class NewUserIsEmpty(Scenario):
    description = "Create a user, log in, and check it owns no documents and no versions"

    def new_user(self, group: Group) -> User:
        """Credentials for the user to create. Override to choose your own."""
        suffix = uuid.uuid4().hex[:8]
        return User(login=f"tester_{suffix}",
                    email=f"tester_{suffix}@example.com",
                    password=secrets.token_urlsafe(12))

    def execute(self, group: Group) -> None:
        user = self.new_user(group)

        # 1. Create the user.
        created = self.step(CreateUser(user))
        self.check_status(created, (200, 201), "create-user succeeds", required=True)
        # The server accepted the write, so the user now belongs in the model.
        if isinstance(created.response_json, dict):
            user.update_from_server(created.response_json)
        group.add_user(user)

        self.check_fields(created, {"id": int, "login": str, "email": str},
                          "create-user returns id, login and email")
        data = created.response_json if isinstance(created.response_json, dict) else {}
        self.check(data.get("login") == user.login and data.get("email") == user.email,
                   "create-user echoes the submitted login and email",
                   {"sent": {"login": user.login, "email": user.email},
                    "received": {"login": data.get("login"), "email": data.get("email")}})

        # 2. Log in.
        logged_in = self.step(Login(user))
        self.check_status(logged_in, 200, "login succeeds", required=True)
        self.check_fields(logged_in, {"token": str, "token_type": str, "expires_in": int},
                          "login returns token, token_type and expires_in")
        data = logged_in.response_json if isinstance(logged_in.response_json, dict) else {}
        self.check(data.get("token_type") == "bearer", "login token_type is 'bearer'",
                   {"token_type": data.get("token_type")})
        expires_in = data.get("expires_in")
        self.check(isinstance(expires_in, int) and not isinstance(expires_in, bool)
                   and expires_in > 0,
                   "login expires_in is a positive TTL", {"expires_in": expires_in})
        self.require(user.token is not None, "a login token is available")

        # 3. No documents.
        documents = self.step(ListDocuments(user))
        if self.check_status(documents, 200, "list-documents succeeds"):
            self.check_fields(documents, {"documents": list},
                              "list-documents returns a documents list")
            listed = _field(documents.response_json, "documents")
            self.check(listed == [], "a new user owns no documents", {"documents": listed})

        # 4. No versions.
        versions = self.step(ListAllVersions(user))
        if self.check_status(versions, 200, "list-all-versions succeeds"):
            self.check_fields(versions, {"versions": list},
                              "list-all-versions returns a versions list")
            listed = _field(versions.response_json, "versions")
            self.check(listed == [], "a new user owns no versions", {"versions": listed})


def _field(data: object, name: str) -> object:
    """data[name] if data is a JSON object, else None."""
    return data.get(name) if isinstance(data, dict) else None
