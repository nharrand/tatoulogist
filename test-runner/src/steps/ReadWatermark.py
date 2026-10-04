"""POST /api/read-watermark/<document_id> and POST /api/read-watermark"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from steps.Step import Step, StepRequest

if TYPE_CHECKING:
    from model.Document import Document
    from model.Group import Group
    from model.User import User


class ReadWatermark(Step):
    """Read the secret watermarked in `document` with `method` and `key`.

    position is omitted when None. With use_path_param=False the document id
    is sent as "id" in the JSON body.
    """

    def __init__(self, user: User | None, document: Document, method: str,
                 key: str | None, position: str | None = None, *,
                 use_path_param: bool = True) -> None:
        self.user = user
        self.document = document
        self.method = method
        self.key = key
        self.position = position
        self.use_path_param = use_path_param

    def build_request(self, group: Group) -> StepRequest:
        body: dict[str, Any] = {"method": self.method, "key": self.key}
        if self.position is not None:
            body["position"] = self.position
        if self.use_path_param:
            path = f"/api/read-watermark/{self.document.id}"
        else:
            path = "/api/read-watermark"
            body["id"] = self.document.id
        return StepRequest("POST", path, json=body, user=self.user)
