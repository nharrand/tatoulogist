"""POST /api/upload-document"""

from __future__ import annotations

from typing import TYPE_CHECKING

from steps.Step import Step, StepRequest

if TYPE_CHECKING:
    from model.Document import Document
    from model.Group import Group
    from model.User import User


class UploadDocument(Step):
    """Upload the local PDF of `document` under `document.name`, as `user`.

    Does not modify the model: on success the scenario fills the document
    from the response and adds it to the user. `content_type` can be changed
    by negative scenarios (e.g. to upload something that is not a PDF).
    """

    def __init__(self, user: User | None, document: Document, *,
                 content_type: str = "application/pdf") -> None:
        self.user = user
        self.document = document
        self.content_type = content_type

    def build_request(self, group: Group) -> StepRequest:
        path = self.document.resolved_path()
        return StepRequest(
            "POST", "/api/upload-document",
            data={"name": self.document.name},
            files={"file": (path.name, path.read_bytes(), self.content_type)},
            user=self.user,
        )
