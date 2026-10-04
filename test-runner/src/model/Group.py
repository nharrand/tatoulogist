"""One API instance under test and the data expected behind it."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from model.User import User

DEFAULT_PORT = 5000
PRIVATE_KEY_FILE = "server_private.asc"
PUBLIC_KEY_FILE = "server_public.asc"
DEFAULT_TIMEOUT = 5.0
DATA_FILE = "data.json"
GROUPS_CSV = "groups.csv"
GROUP_ID_PATTERN = re.compile(r"^Group_\d+$")


@dataclass(frozen=True)
class GroupRow:
    """One line of groups.csv."""

    name: str
    ip: str
    members: int | None = None


@dataclass
class Group:
    """Connection information and expected data for one API instance.

    On disk, a group lives in <data_root>/Groups/<name>/:
        data.json       users -> documents -> versions known to be on the server
        pdfs/           copies of the PDFs uploaded to the server
        pdfs/versions/  watermarked PDFs downloaded from the server

    The IP and the number of members are not stored in data.json, because
    groups.csv is the source of truth for them.
    """

    name: str
    ip: str
    data_root: Path
    members: int | None = None      # "Members" column of groups.csv
    port: int = DEFAULT_PORT
    timeout: float = DEFAULT_TIMEOUT
    users: list[User] = field(default_factory=list)

    # Run settings for RMAP (from the CLI, never serialized).
    rmap_identity: str | None = field(default=None, compare=False)
    key_passphrase: str | None = field(default=None, repr=False, compare=False)

    _session: requests.Session | None = field(default=None, init=False,
                                              repr=False, compare=False)

    def __post_init__(self) -> None:
        self.data_root = Path(self.data_root)
        for user in self.users:
            user.group = self

    # ----------------------------------------------------------- connection

    @property
    def base_url(self) -> str:
        return f"http://{self.ip}:{self.port}"

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    @property
    def session(self) -> requests.Session:
        """HTTP session shared by every step run against this group."""
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    # ---------------------------------------------------------------- paths

    @property
    def directory(self) -> Path:
        return self.data_root / "Groups" / self.name

    @property
    def data_file(self) -> Path:
        return self.directory / DATA_FILE

    @property
    def pdfs_dir(self) -> Path:
        return self.directory / "pdfs"

    @property
    def versions_dir(self) -> Path:
        return self.pdfs_dir / "versions"

    @property
    def keys_dir(self) -> Path:
        return self.data_root / "keys"

    @property
    def public_key_path(self) -> Path:
        """This group's server public key, used to encrypt RMAP messages."""
        return self.keys_dir / "public_keys" / f"{self.name}.asc"

    @property
    def private_key_path(self) -> Path:
        """The test bench's private key, used to decrypt RMAP responses."""
        return self.keys_dir / PRIVATE_KEY_FILE

    @property
    def rmap_dir(self) -> Path:
        """PDFs retrieved through RMAP links."""
        return self.pdfs_dir / "rmap"

    def resolve(self, path: str | Path) -> Path:
        """Resolve a path stored in data.json (relative to the group directory)."""
        path = Path(path)
        return path if path.is_absolute() else self.directory / path

    def relative(self, path: Path) -> str:
        """Express a path inside the group directory as stored in data.json."""
        return Path(path).resolve().relative_to(self.directory.resolve()).as_posix()

    def import_pdf(self, source: str | Path, filename: str | None = None) -> str:
        """Copy a PDF into pdfs/ (never overwriting) and return its stored path."""
        source = Path(source)
        target = _unique_path(self.pdfs_dir, filename or source.name)
        shutil.copyfile(source, target)
        return self.relative(target)

    def write_version_pdf(self, content: bytes, filename: str) -> str:
        """Save a downloaded watermarked PDF into pdfs/versions/ and return its stored path."""
        target = _unique_path(self.versions_dir, filename)
        target.write_bytes(content)
        return self.relative(target)

    def write_rmap_pdf(self, content: bytes, filename: str) -> str:
        """Save a PDF retrieved through an RMAP link into pdfs/rmap/."""
        target = _unique_path(self.rmap_dir, filename)
        target.write_bytes(content)
        return self.relative(target)

    # ---------------------------------------------------------------- users

    def add_user(self, user: User) -> User:
        user.group = self
        self.users.append(user)
        return user

    def remove_user(self, user: User) -> None:
        self.users.remove(user)
        user.group = None

    def find_user(self, *, login: str | None = None, email: str | None = None,
                  id: int | None = None) -> User | None:
        for user in self.users:
            if login is not None and user.login == login:
                return user
            if email is not None and user.email == email:
                return user
            if id is not None and str(user.id) == str(id):
                return user
        return None

    # -------------------------------------------------------- serialization

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "users": [u.to_dict() for u in self.users]}

    def initialize(self) -> None:
        """Create the group directory, pdfs folders and an empty data.json if missing."""
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        if not self.data_file.exists():
            self.save()

    def save(self) -> None:
        """Write data.json atomically."""
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".data.", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, self.data_file)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, name: str, ip: str, data_root: str | Path, *,
             members: int | None = None, port: int = DEFAULT_PORT,
             timeout: float = DEFAULT_TIMEOUT, rmap_identity: str | None = None,
             key_passphrase: str | None = None) -> Group:
        """Load a group from its data.json, initializing its folder if needed."""
        group = cls(name=name, ip=ip, data_root=Path(data_root), members=members,
                    port=port, timeout=timeout, rmap_identity=rmap_identity,
                    key_passphrase=key_passphrase)
        group.initialize()
        with group.data_file.open(encoding="utf-8") as fh:
            data = json.load(fh)
        stored_name = data.get("name", name)
        if stored_name != name:
            raise ValueError(f"{group.data_file} belongs to {stored_name!r}, not {name!r}")
        for entry in data.get("users", []):
            group.add_user(User.from_dict(entry))
        return group

    @staticmethod
    def read_csv(csv_path: str | Path) -> list[GroupRow]:
        """Read groups.csv: columns Group and IP, plus an optional Members column.

        When the Members column is present, every row must hold a
        non-negative integer in it.
        """
        rows: list[GroupRow] = []
        with Path(csv_path).open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            reader.fieldnames = [f.strip() for f in reader.fieldnames or []]
            if not {"Group", "IP"} <= set(reader.fieldnames):
                raise ValueError(f"{csv_path} must have 'Group' and 'IP' columns")
            has_members = "Members" in reader.fieldnames
            for line, row in enumerate(reader, start=2):
                name, ip = (row.get("Group") or "").strip(), (row.get("IP") or "").strip()
                raw_members = (row.get("Members") or "").strip()
                if not name and not ip and not raw_members:
                    continue
                if not GROUP_ID_PATTERN.match(name):
                    raise ValueError(f"{csv_path}:{line}: invalid group id {name!r}")
                if not ip:
                    raise ValueError(f"{csv_path}:{line}: missing IP for {name}")
                members = None
                if has_members:
                    if not raw_members.isdigit():
                        raise ValueError(f"{csv_path}:{line}: Members for {name} must be "
                                         f"a non-negative integer, got {raw_members!r}")
                    members = int(raw_members)
                rows.append(GroupRow(name, ip, members))
        return rows

    @classmethod
    def from_row(cls, row: GroupRow, data_root: str | Path, *,
                 timeout: float = DEFAULT_TIMEOUT, rmap_identity: str | None = None,
                 key_passphrase: str | None = None) -> Group:
        return cls.load(row.name, row.ip, data_root, members=row.members, timeout=timeout,
                        rmap_identity=rmap_identity, key_passphrase=key_passphrase)

    @classmethod
    def load_all(cls, data_root: str | Path, *, names: list[str] | None = None,
                 timeout: float = DEFAULT_TIMEOUT) -> list[Group]:
        """Load the groups listed in <data_root>/groups.csv (optionally only `names`)."""
        data_root = Path(data_root)
        rows = cls.read_csv(data_root / GROUPS_CSV)
        if names is not None:
            known = {row.name for row in rows}
            missing = [n for n in names if n not in known]
            if missing:
                raise KeyError(f"not in {GROUPS_CSV}: {', '.join(missing)}")
            rows = [row for row in rows if row.name in names]
        return [cls.from_row(row, data_root, timeout=timeout) for row in rows]


def _unique_path(directory: Path, filename: str) -> Path:
    """Return directory/filename, adding a _N suffix if that file already exists."""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / Path(filename).name
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate
