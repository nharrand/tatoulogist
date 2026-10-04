"""A user account behind one API instance."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from model.Document import Document

if TYPE_CHECKING:
    from model.Group import Group


@dataclass
class User:
    """A user account and the documents it owns.

    `login` is the user's name (the API calls it "login"). The session token
    is cached here at runtime and is never written to data.json.
    """

    login: str
    email: str
    password: str
    id: int | None = None
    documents: list[Document] = field(default_factory=list)

    # Back-reference, set by Group. Not serialized.
    group: Group | None = field(default=None, repr=False, compare=False)

    # Runtime-only session state.
    token: str | None = field(default=None, repr=False, compare=False)
    token_expires_at: float | None = field(default=None, repr=False, compare=False)

    _SERVER_FIELDS = ("id", "login", "email")

    def __post_init__(self) -> None:
        for document in self.documents:
            document.owner = self

    # ---------------------------------------------------------------- token

    def set_token(self, token: str, expires_in: int | float | None = None) -> None:
        """Cache a token returned by login. expires_in is the TTL in seconds."""
        self.token = token
        self.token_expires_at = (time.monotonic() + expires_in
                                 if expires_in is not None else None)

    def clear_token(self) -> None:
        self.token = None
        self.token_expires_at = None

    def has_valid_token(self, margin: float = 5.0) -> bool:
        """True if a token is cached and will not expire within `margin` seconds."""
        if self.token is None:
            return False
        if self.token_expires_at is None:
            return True
        return time.monotonic() + margin < self.token_expires_at

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    # ------------------------------------------------------------ documents

    def add_document(self, document: Document) -> Document:
        document.owner = self
        self.documents.append(document)
        return document

    def remove_document(self, document: Document) -> None:
        self.documents.remove(document)
        document.owner = None

    def find_document(self, *, id: int | str | None = None,
                      name: str | None = None) -> Document | None:
        for document in self.documents:
            if id is not None and str(document.id) == str(id):
                return document
            if name is not None and document.name == name:
                return document
        return None

    # -------------------------------------------------------- serialization

    def update_from_server(self, data: dict[str, Any]) -> None:
        """Copy the fields present in a create-user response."""
        for name in self._SERVER_FIELDS:
            if data.get(name) is not None:
                setattr(self, name, data[name])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "login": self.login,
            "email": self.email,
            "password": self.password,
            "documents": [d.to_dict() for d in self.documents],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], group: Group | None = None) -> User:
        user = cls(
            login=data["login"],
            email=data["email"],
            password=data["password"],
            id=data.get("id"),
            group=group,
        )
        for entry in data.get("documents", []):
            user.add_document(Document.from_dict(entry))
        return user
