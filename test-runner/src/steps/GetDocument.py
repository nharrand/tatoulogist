"""GET /api/get-document/<document_id> and GET /api/get-document?id=..."""

from __future__ import annotations

from typing import TYPE_CHECKING

from steps.Step import Step, StepRequest

if TYPE_CHECKING:
    from model.Document import Document
    from model.Group import Group
    from model.User import User


class GetDocument(Step):
    """Download the original PDF of `document` (in result.content) as `user`.

    With use_path_param=True (default) the id goes in the path; otherwise it
    is sent as the `id` query parameter. Pass user=None to send no token.
    """

    def __init__(self, user: User | None, document: Document, *,
                 use_path_param: bool = True) -> None:
        self.user = user
        self.document = document
        self.use_path_param = use_path_param

    def build_request(self, group: Group) -> StepRequest:
        if self.use_path_param:
            return StepRequest("GET", f"/api/get-document/{self.document.id}",
                               user=self.user)
        return StepRequest("GET", "/api/get-document",
                           params={"id": self.document.id}, user=self.user)
