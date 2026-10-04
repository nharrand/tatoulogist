"""Make sure the user Mr_Important exists and owns a document called flag_3."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any, ClassVar

from model.Document import Document
from model.User import User
from scenarii.Scenario import Scenario
from steps.CreateUser import CreateUser
from steps.ListDocuments import ListDocuments
from steps.Login import Login
from steps.Step import StepResult
from steps.UploadDocument import UploadDocument

if TYPE_CHECKING:
    from model.Group import Group


class MrImportant(Scenario):
    """1. Use the user Mr_Important from data.json, or create it on the server.
    2. Log in and list its documents.
    3. If a document called flag_3 is already there, the scenario passes on the
       user and the document existing; the local flag_3.pdf is not needed and
       its bytes are not compared. Otherwise it uploads
       <group directory>/flag_3.pdf (which must exist) under the name flag_3
       and checks that it is then listed.

    So once the flag has been uploaded, the local flag_3.pdf can be deleted
    and the scenario still passes. The file is only required when there is
    nothing on the server yet to satisfy the check.

    The local model is kept in line with the server:
    - a flag_3 listed by the server but unknown locally is recorded;
    - a local flag_3 the server no longer lists is removed.
    """

    name = "Mr_Important"
    description = "Make sure user Mr_Important exists and owns a document called flag_3"

    LOGIN: ClassVar[str] = "Mr_Important"
    EMAIL: ClassVar[str] = "mr_important@example.com"
    #: Password used when creating the user. None generates a random one,
    #: which is then only known through data.json.
    PASSWORD: ClassVar[str | None] = None

    DOCUMENT_NAME: ClassVar[str] = "flag_3"
    #: The flag PDF, relative to the group directory (data/Groups/<group>/).
    FLAG_FILE: ClassVar[str] = "flag_3.pdf"

    def execute(self, group: Group) -> None:
        user = self.get_user(group)
        login = self.step(Login(user))
        self.check_status(login, 200, f"login as {user.login} succeeds", required=True)
        self.require(user.token is not None, "login returns a token")
        if user.group is None:  # existed on the server but not in data.json
            group.add_user(user)

        entries = self.list_documents(user)
        flags = [e for e in entries if e.get("name") == self.DOCUMENT_NAME]
        self.forget_stale_flags(user, {str(e.get("id")) for e in flags})

        if flags:
            # Already uploaded: the user and the document exist, which is enough.
            # The local file is not needed and its content is not checked.
            self.check(len(flags) == 1, f"exactly one document is called {self.DOCUMENT_NAME}",
                       {"ids": [e.get("id") for e in flags]})
            entry = flags[0]
            self.check(True, f"{user.login} owns a document called {self.DOCUMENT_NAME}",
                       {"id": entry.get("id")})
            if user.find_document(id=entry.get("id")) is None:
                self.record(group, user, entry)
            return

        # Nothing on the server yet: the flag file is needed to upload it.
        flag = group.directory / self.FLAG_FILE
        self.require(flag.is_file(),
                     f"{self.FLAG_FILE} exists in the group directory, to be uploaded",
                     {"path": str(flag)})
        self.upload_flag(group, user)

    # ------------------------------------------------------------- helpers

    def get_user(self, group: Group) -> User:
        """The user from data.json, else create it (accepting 'already exists')."""
        user = group.find_user(login=self.LOGIN)
        if user is not None:
            return user
        user = User(login=self.LOGIN, email=self.EMAIL,
                    password=self.PASSWORD or secrets.token_urlsafe(16))
        created = self.step(CreateUser(user))
        self.check_status(created, (200, 201, 409),
                          f"create-user {self.LOGIN} succeeds or the user already exists")
        if created.ok:
            user.update_from_server(_obj(created))
            group.add_user(user)
        return user

    def list_documents(self, user: User) -> list[dict[str, Any]]:
        result = self.step(ListDocuments(user))
        self.check_status(result, 200, "list-documents succeeds", required=True)
        self.check_fields(result, {"documents": list},
                          "list-documents returns a documents list", required=True)
        return [e for e in result.response_json["documents"] if isinstance(e, dict)]

    def forget_stale_flags(self, user: User, listed_ids: set[str]) -> None:
        """Remove local flag documents that the server does not list any more."""
        group = user.group
        for document in [d for d in user.documents if d.name == self.DOCUMENT_NAME
                         and str(d.id) not in listed_ids]:
            path = document.resolved_path()
            user.remove_document(document)
            # Only delete the bench's own copy, never the master flag_3.pdf.
            if group is not None and path.resolve().parent == group.pdfs_dir.resolve():
                path.unlink(missing_ok=True)

    def record(self, group: Group, user: User, entry: dict[str, Any]) -> Document:
        """Add a flag document that is on the server to the local model.

        The master flag_3.pdf is copied into pdfs/ if it is present; if it has
        been removed, the document is recorded with the server's metadata and
        its path points to where the flag file would be.
        """
        document = Document(name=self.DOCUMENT_NAME, path="")
        document.update_from_server(entry)
        flag = group.directory / self.FLAG_FILE
        if flag.is_file():
            document.path = group.import_pdf(flag, f"{document.id}_{flag.name}")
        else:
            document.path = group.relative(flag)
        return user.add_document(document)

    def upload_flag(self, group: Group, user: User) -> None:
        flag = group.directory / self.FLAG_FILE
        document = Document(name=self.DOCUMENT_NAME, path=str(flag))
        result = self.step(UploadDocument(user, document))
        self.check_status(result, (200, 201), f"upload of {self.FLAG_FILE} succeeds",
                          required=True)
        data = _obj(result)
        self.require(data.get("id") is not None, "upload-document returns the document id",
                     {"response": result.response_json})
        self.record(group, user, data)

        self.check(data.get("name") == self.DOCUMENT_NAME,
                   f"uploaded document is called {self.DOCUMENT_NAME}",
                   {"server": data.get("name")})

        listed = self.list_documents(user)
        self.check(any(str(e.get("id")) == str(data["id"]) for e in listed),
                   f"{self.DOCUMENT_NAME} is listed after upload")


def _obj(result: StepResult) -> dict[str, Any]:
    return result.response_json if isinstance(result.response_json, dict) else {}
