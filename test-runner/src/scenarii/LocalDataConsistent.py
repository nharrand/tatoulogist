"""Check that the local data of a group matches what its server reports."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from scenarii.Scenario import Scenario
from steps.ListDocuments import ListDocuments
from steps.ListVersions import ListVersions
from steps.Login import Login
from steps.Step import StepResult

if TYPE_CHECKING:
    from model.Document import Document
    from model.Group import Group
    from model.User import User
    from model.Version import Version


class LocalDataConsistent(Scenario):
    """For every local user: log in and compare list-documents with the local
    documents. For every document: compare list-versions with the local
    versions. Both directions are checked: everything local must be on the
    server, and the server must list nothing unknown locally.

    Read-only. The local model is never modified. A problem with one user or
    document is recorded and the scenario moves on to the next one.
    """

    description = "Check that all local users, documents and versions match the server"

    DOCUMENT_FIELDS = ("name", "creation", "sha256", "size")
    VERSION_FIELDS = ("link", "intended_for", "secret", "method")

    def execute(self, group: Group) -> None:
        if not group.users:
            self.check(True, "no local users to verify")
            return
        for user in group.users:
            self.verify_user(user)

    # ---------------------------------------------------------------- users

    def verify_user(self, user: User) -> None:
        label = f"user {user.login}"

        if not user.has_valid_token():
            login = self.step(Login(user))
            if not self.check_status(login, 200, f"{label}: login succeeds"):
                return
            if not self.check(user.token is not None, f"{label}: login returns a token"):
                return

        listing = self.step(ListDocuments(user))
        remote = self.remote_entries(listing, "documents", f"{label}: list-documents")
        if remote is None:
            return

        for document in user.documents:
            self.verify_document(user, document, remote.get(str(document.id)))

        local_ids = {str(d.id) for d in user.documents}
        unknown = sorted(set(remote) - local_ids)
        self.check(not unknown, f"{label}: server lists no document unknown locally",
                   {"unknown": [remote[i] for i in unknown]} if unknown else None)

    # ------------------------------------------------------------ documents

    def verify_document(self, user: User, document: Document,
                        entry: dict[str, Any] | None) -> None:
        label = f"user {user.login}, document {document.id} ({document.name})"

        if not self.check(entry is not None, f"{label}: listed by the server"):
            return
        diffs = _diff(document, entry, self.DOCUMENT_FIELDS)
        self.check(not diffs, f"{label}: metadata matches the server", diffs or None)
        self.verify_local_document_file(label, document)

        listing = self.step(ListVersions(user, document))
        remote = self.remote_entries(listing, "versions", f"{label}: list-versions")
        if remote is None:
            return

        for version in document.versions:
            self.verify_version(label, document, version, remote.get(str(version.id)))

        local_ids = {str(v.id) for v in document.versions}
        unknown = sorted(set(remote) - local_ids)
        self.check(not unknown, f"{label}: server lists no version unknown locally",
                   {"unknown": [remote[i] for i in unknown]} if unknown else None)

    def verify_local_document_file(self, label: str, document: Document) -> None:
        path = document.resolved_path()
        if not self.check(path.is_file(), f"{label}: local PDF exists", {"path": str(path)}):
            return
        if document.sha256 is not None:
            local = document.local_sha256()
            same = local == str(document.sha256).lower()
            self.check(same, f"{label}: local PDF matches the server's sha256",
                       None if same else {"local": local, "server": document.sha256})

    # ------------------------------------------------------------- versions

    def verify_version(self, document_label: str, document: Document, version: Version,
                       entry: dict[str, Any] | None) -> None:
        label = f"{document_label}, version {version.id}"

        if not self.check(entry is not None, f"{label}: listed by the server"):
            return
        diffs = _diff(version, entry, self.VERSION_FIELDS)
        if "documentid" in entry and str(entry["documentid"]) != str(document.id):
            diffs["documentid"] = {"local": document.id, "server": entry["documentid"]}
        self.check(not diffs, f"{label}: metadata matches the server", diffs or None)

        path = version.resolved_path()
        if path is not None:
            self.check(path.is_file(), f"{label}: local watermarked PDF exists",
                       {"path": str(path)})

    # -------------------------------------------------------------- helpers

    def remote_entries(self, result: StepResult, key: str,
                       label: str) -> dict[str, dict[str, Any]] | None:
        """Check a listing response and index its entries by id (as strings)."""
        if not self.check_status(result, 200, f"{label} succeeds"):
            return None
        if not self.check_fields(result, {key: list}, f"{label} returns a {key} list"):
            return None
        entries = result.response_json[key]
        indexed = {str(e["id"]): e for e in entries if isinstance(e, dict) and "id" in e}
        malformed = [e for e in entries if not (isinstance(e, dict) and "id" in e)]
        if malformed:
            self.check(False, f"{label}: every entry has an id", {"malformed": malformed})
        if len(indexed) + len(malformed) != len(entries):
            self.check(False, f"{label}: entry ids are unique",
                       {"entries": len(entries), "distinct_ids": len(indexed)})
        return indexed


def _diff(local: object, entry: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Fields whose local value is known and differs from the server's value."""
    diffs: dict[str, Any] = {}
    for name in fields:
        expected = getattr(local, name)
        if expected is None:
            continue
        actual = entry.get(name)
        if not _same(name, expected, actual):
            diffs[name] = {"local": expected, "server": actual}
    return diffs


def _same(name: str, expected: Any, actual: Any) -> bool:
    if name == "creation":
        a, b = _parse_datetime(expected), _parse_datetime(actual)
        if a is not None and b is not None:
            return a == b
    return expected == actual


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO 8601 string, accepting a trailing 'Z' (Python 3.10 does not)."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
