"""Maintenance: drop from the local data everything the server no longer has."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

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

log = logging.getLogger("tatou")


class RealignLocalData(Scenario):
    """For every local user:

    - log in; if the server rejects the credentials, remove the user;
    - list its documents and remove the local documents that are not listed;
    - for every remaining document, list its versions and remove the local
      versions that are not listed.

    The bench's own copies of removed PDFs (under the group's pdfs/ folder)
    are deleted too. Nothing is added: items the server has but the local
    data lacks are left alone (LocalDataConsistent reports them).

    Safety: data is only removed on a definite answer from the server. A
    user is removed only if login answers one of REJECTED_LOGIN; on a
    timeout, a connection error, a 5xx or a failed listing, the user (or
    document) is kept and the problem is recorded as a failed check.

    This is a maintenance tool: `--scenario all` does not run it.
    """

    description = "Remove local users, documents and versions that the server no longer has"
    maintenance = True

    #: Login status codes meaning "these credentials are not valid here".
    REJECTED_LOGIN: ClassVar[frozenset[int]] = frozenset({400, 401, 403, 404})

    def __init__(self) -> None:
        super().__init__()
        self.removed: dict[str, list[Any]] = {"users": [], "documents": [], "versions": []}

    def execute(self, group: Group) -> None:
        self.removed = {"users": [], "documents": [], "versions": []}
        if not group.users:
            self.check(True, "no local users to realign")
            return

        for user in list(group.users):
            self.realign_user(group, user)

        self.check(True, "local data realigned with the server",
                   {"removed": self.removed,
                    "remaining": {"users": len(group.users),
                                  "documents": sum(len(u.documents) for u in group.users),
                                  "versions": sum(len(d.versions) for u in group.users
                                                  for d in u.documents)}})

    # ---------------------------------------------------------------- users

    def realign_user(self, group: Group, user: User) -> None:
        label = f"user {user.login}"
        login = self.step(Login(user))

        if login.status_code in self.REJECTED_LOGIN:
            self.remove_user(group, user, f"login rejected with {login.status_code}")
            return
        if not self.check(login.ok and user.token is not None, f"{label}: login succeeds",
                          {"status_code": login.status_code, "error": login.error,
                           "response": login.response_json}):
            return  # no definite answer: keep the user

        remote = self.listed_ids(self.step(ListDocuments(user)), "documents",
                                 f"{label}: list-documents")
        if remote is None:
            return
        for document in list(user.documents):
            if str(document.id) not in remote:
                self.remove_document(group, user, document)
            else:
                self.realign_document(group, user, document)

    def remove_user(self, group: Group, user: User, reason: str) -> None:
        for document in list(user.documents):
            self.delete_document_files(group, document)
        group.remove_user(user)
        self.removed["users"].append(user.login)
        log.info("[%s]   removed user %s (%s)", group.name, user.login, reason)
        self.check(True, f"user {user.login}: {reason}, removed with its "
                         f"{len(user.documents)} document(s)")

    # ------------------------------------------------------------ documents

    def realign_document(self, group: Group, user: User, document: Document) -> None:
        label = f"user {user.login}, document {document.id} ({document.name})"
        remote = self.listed_ids(self.step(ListVersions(user, document)), "versions",
                                 f"{label}: list-versions")
        if remote is None:
            return
        for version in list(document.versions):
            if str(version.id) not in remote:
                self.remove_version(group, user, document, version)

    def remove_document(self, group: Group, user: User, document: Document) -> None:
        self.delete_document_files(group, document)
        user.remove_document(document)
        self.removed["documents"].append({"user": user.login, "id": document.id,
                                          "name": document.name})
        log.info("[%s]   removed document %s (%s) of %s", group.name, document.id,
                 document.name, user.login)
        self.check(True, f"user {user.login}, document {document.id} ({document.name}): "
                         f"not listed by the server, removed with its "
                         f"{len(document.versions)} version(s)")

    def remove_version(self, group: Group, user: User, document: Document,
                       version: Version) -> None:
        _delete_local_copy(group, version.resolved_path())
        document.remove_version(version)
        self.removed["versions"].append({"user": user.login, "document": document.id,
                                         "id": version.id, "link": version.link})
        log.info("[%s]   removed version %s of document %s", group.name, version.id,
                 document.id)
        self.check(True, f"user {user.login}, document {document.id}, version "
                         f"{version.id}: not listed by the server, removed")

    def delete_document_files(self, group: Group, document: Document) -> None:
        _delete_local_copy(group, document.resolved_path())
        for version in document.versions:
            _delete_local_copy(group, version.resolved_path())

    # -------------------------------------------------------------- helpers

    def listed_ids(self, result: StepResult, key: str, label: str) -> set[str] | None:
        """Ids (as strings) of a listing, or None if the listing is unusable."""
        if not self.check_status(result, 200, f"{label} succeeds"):
            return None
        entries = result.response_json.get(key) \
            if isinstance(result.response_json, dict) else None
        if not self.check(isinstance(entries, list) and all(
                isinstance(e, dict) and "id" in e for e in entries),
                f"{label} returns a {key} list with ids",
                {"response": result.response_json}):
            return None
        return {str(e["id"]) for e in entries}


def _delete_local_copy(group: Group, path: Path | None) -> None:
    """Delete a file only if it is one of the bench's copies under pdfs/."""
    if path is None:
        return
    resolved = path.resolve()
    if resolved.is_relative_to(group.pdfs_dir.resolve()) and resolved.is_file():
        resolved.unlink()
