"""DELETE /api/delete-document/<document_id> and DELETE|POST /api/delete-document"""

from __future__ import annotations

from typing import TYPE_CHECKING

from steps.Step import Step, StepRequest

if TYPE_CHECKING:
    from model.Document import Document
    from model.Group import Group
    from model.User import User


class DeleteDocument(Step):
    """Delete `document` (and, server side, its versions) as `user`.

    Variants:
        use_path_param=True  -> DELETE /api/delete-document/<id>
        use_path_param=False -> <http_method> /api/delete-document with {"id": <id>}
    Does not modify the model: on success the scenario removes the document.
    """

    def __init__(self, user: User | None, document: Document, *,
                 use_path_param: bool = True, http_method: str = "DELETE") -> None:
        if http_method.upper() not in ("DELETE", "POST"):
            raise ValueError("http_method must be DELETE or POST")
        self.user = user
        self.document = document
        self.use_path_param = use_path_param
        self.http_method = http_method.upper()

    def build_request(self, group: Group) -> StepRequest:
        if self.use_path_param:
            return StepRequest("DELETE", f"/api/delete-document/{self.document.id}",
                               user=self.user)
        return StepRequest(self.http_method, "/api/delete-document",
                           json={"id": self.document.id}, user=self.user)
