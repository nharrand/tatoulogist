"""A PDF document owned by a user."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from model.Version import Version

if TYPE_CHECKING:
    from model.Group import Group
    from model.User import User


@dataclass
class Document:
    """A PDF document, with the local file and the server's view of it.

    `path` points to the local PDF. Once the document has been uploaded, it
    is relative to the group directory, e.g. "pdfs/3_report.pdf". Before
    upload it can be any path, typically one in the shared pool data/pdfs.

    `sha256` and `size` are the values the server returned. Compare them
    with local_sha256() and local_size() to verify an upload.
    """

    name: str
    path: str

    # Returned by the server. The API is inconsistent about the type of id
    # (string in upload/list, int in the routes), so it is stored as received.
    id: int | str | None = None
    creation: str | None = None
    sha256: str | None = None
    size: int | None = None

    versions: list[Version] = field(default_factory=list)

    # Back-reference, set by User. Not serialized.
    owner: User | None = field(default=None, repr=False, compare=False)

    _SERVER_FIELDS = ("id", "name", "creation", "sha256", "size")

    def __post_init__(self) -> None:
        for version in self.versions:
            version.document = self

    # ---------------------------------------------------------------- files

    @property
    def group(self) -> Group | None:
        return self.owner.group if self.owner is not None else None

    def resolved_path(self) -> Path:
        """Absolute path of the local PDF."""
        group = self.group
        return group.resolve(self.path) if group is not None else Path(self.path)

    def read_bytes(self) -> bytes:
        return self.resolved_path().read_bytes()

    def local_sha256(self) -> str:
        return hashlib.sha256(self.read_bytes()).hexdigest()

    def local_size(self) -> int:
        return self.resolved_path().stat().st_size

    # ------------------------------------------------------------- versions

    def add_version(self, version: Version) -> Version:
        version.document = self
        self.versions.append(version)
        return version

    def remove_version(self, version: Version) -> None:
        self.versions.remove(version)
        version.document = None

    def find_version(self, *, id: int | str | None = None,
                     link: str | None = None) -> Version | None:
        for version in self.versions:
            if id is not None and str(version.id) == str(id):
                return version
            if link is not None and version.link == link:
                return version
        return None

    # -------------------------------------------------------- serialization

    def update_from_server(self, data: dict[str, Any]) -> None:
        """Copy the fields present in an upload-document or list-documents entry."""
        for name in self._SERVER_FIELDS:
            if data.get(name) is not None:
                setattr(self, name, data[name])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "creation": self.creation,
            "sha256": self.sha256,
            "size": self.size,
            "path": self.path,
            "versions": [v.to_dict() for v in self.versions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], owner: User | None = None) -> Document:
        document = cls(
            name=data["name"],
            path=data["path"],
            id=data.get("id"),
            creation=data.get("creation"),
            sha256=data.get("sha256"),
            size=data.get("size"),
            owner=owner,
        )
        for entry in data.get("versions", []):
            document.add_version(Version.from_dict(entry))
        return document
