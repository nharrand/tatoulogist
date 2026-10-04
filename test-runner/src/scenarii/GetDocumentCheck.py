"""Check get-document on a document owned by a local user."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from scenarii.WatermarkLifecycle import WatermarkLifecycle
from steps.GetDocument import GetDocument
from steps.Login import Login
from steps.Step import StepResult

if TYPE_CHECKING:
    from model.Document import Document
    from model.Group import Group
    from model.User import User

log = logging.getLogger("tatou")


class GetDocumentCheck(WatermarkLifecycle):
    """1. Pick a local user that owns a document. If there is none, take an
       existing user (or create one) and upload a PDF from data/pdfs.
    2. Log in as the owner and download the document with both route forms,
       /api/get-document/<id> and /api/get-document?id=<id>. Each must return
       the PDF that was uploaded (same sha256 as the server recorded and as
       the local copy).
    3. Without a token, the download must be refused.
    4. If another local user can log in, the download must be refused for
       that user too (an original document is only for its owner).

    Inherits the user and upload helpers of WatermarkLifecycle.
    """

    description = "Check that get-document returns the owner's PDF, and only to the owner"

    def execute(self, group: Group) -> None:
        owner, document = self.find_document(group)
        if document is None:
            owner = self.get_user(group)
            self.login(owner)
            document = self.upload_source(group, owner)
        else:
            log.info("[%s]   using document %s (%s) of %s", group.name, document.id,
                     document.name, owner.login)
            self.login(owner)

        for use_path_param in (True, False):
            self.download(owner, document, use_path_param)

        self.refused(GetDocument(None, document), "get-document without a token is refused")

        other = self.other_user(group, owner)
        if other is not None:
            self.refused(GetDocument(other, document),
                         f"get-document of {owner.login}'s document is refused "
                         f"to {other.login}")

    # -------------------------------------------------------------- helpers

    def find_document(self, group: Group) -> tuple[User | None, Document | None]:
        for user in group.users:
            if user.documents:
                return user, user.documents[0]
        return None, None

    def download(self, owner: User, document: Document, use_path_param: bool) -> None:
        route = (f"get-document/{document.id}" if use_path_param
                 else f"get-document?id={document.id}")
        result = self.step(GetDocument(owner, document, use_path_param=use_path_param))
        if not self.check_status(result, 200, f"{route} succeeds for the owner"):
            return
        content = result.content or b""
        if not self.check(content.startswith(b"%PDF"), f"{route} returns a PDF",
                          {"content_type": result.content_type, "length": len(content)}):
            return
        self.check("application/pdf" in (result.content_type or ""),
                   f"{route} is served as application/pdf",
                   {"content_type": result.content_type})

        sha = hashlib.sha256(content).hexdigest()
        if document.sha256 is not None:
            self.check(sha == str(document.sha256).lower(),
                       f"{route} returns the uploaded file (server sha256)",
                       {"expected": document.sha256, "downloaded": sha})
        if document.resolved_path().is_file():
            local = document.local_sha256()
            self.check(sha == local, f"{route} returns the uploaded file (local copy)",
                       {"expected": local, "downloaded": sha})

    def other_user(self, group: Group, owner: User) -> User | None:
        """Another local user that can log in, if any."""
        for user in group.users:
            if user is owner:
                continue
            if user.has_valid_token() or self.step(Login(user)).ok:
                return user
        return None

    def refused(self, step: GetDocument, description: str) -> None:
        result = self.step(step)
        leaked = result.ok and (result.content or b"").startswith(b"%PDF")
        self.check(result.responded and not leaked, description, _status(result))
        self.check(result.responded and result.status_code < 500,
                   f"{description} without a server error", _status(result))


def _status(result: StepResult) -> dict:
    return {"status_code": result.status_code, "error": result.error,
            "content_type": result.content_type}
