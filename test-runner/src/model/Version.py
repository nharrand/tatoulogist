"""A watermarked version of a document."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from model.Document import Document


@dataclass
class Version:
    """A watermarked version of a Document, created through create-watermark.

    The request parameters (method, position, key, secret, intended_for) are
    kept as they were sent, because the server never echoes key or secret
    back. Server-assigned fields (id, link, filename, size) are filled in
    from the server's response with update_from_server().
    """

    method: str
    position: str | None = None
    key: str | None = None
    secret: str | None = None
    intended_for: str | None = None

    # Assigned by the server.
    id: int | None = None
    link: str | None = None
    filename: str | None = None
    size: int | None = None

    # Local copy of the watermarked PDF, relative to the group directory.
    path: str | None = None

    # Back-reference, set by Document. Not serialized.
    document: Document | None = field(default=None, repr=False, compare=False)

    _SERVER_FIELDS = ("id", "link", "filename", "size",
                      "method", "position", "intended_for", "secret")
    _SERIALIZED = ("id", "link", "method", "position", "key", "secret",
                   "intended_for", "filename", "size", "path")

    def update_from_server(self, data: dict[str, Any]) -> None:
        """Copy the fields present in a create-watermark or list-versions entry."""
        for name in self._SERVER_FIELDS:
            if data.get(name) is not None:
                setattr(self, name, data[name])

    def resolved_path(self) -> Path | None:
        """Absolute path of the local watermarked PDF, if one was downloaded."""
        if self.path is None:
            return None
        group = self.document.group if self.document is not None else None
        return group.resolve(self.path) if group is not None else Path(self.path)

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self._SERIALIZED}

    @classmethod
    def from_dict(cls, data: dict[str, Any], document: Document | None = None) -> Version:
        kwargs = {name: data.get(name) for name in cls._SERIALIZED}
        if kwargs["method"] is None:
            raise ValueError(f"version entry without a method: {data!r}")
        return cls(**kwargs, document=document)
