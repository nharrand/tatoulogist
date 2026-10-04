"""POST /api/create-watermark/<document_id> and POST /api/create-watermark"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from steps.Step import Step, StepRequest

if TYPE_CHECKING:
    from model.Document import Document
    from model.Group import Group
    from model.User import User
    from model.Version import Version


class CreateWatermark(Step):
    """Create a watermarked version of `document` with the parameters of `version`.

    `version` holds what to send (method, position, key, secret,
    intended_for); position is omitted when None. Does not modify the
    model: on success the scenario fills the version from the response and
    adds it to the document. With use_path_param=False the document id is
    sent as "id" in the JSON body.
    """

    def __init__(self, user: User | None, document: Document, version: Version, *,
                 use_path_param: bool = True) -> None:
        self.user = user
        self.document = document
        self.version = version
        self.use_path_param = use_path_param

    def build_request(self, group: Group) -> StepRequest:
        body: dict[str, Any] = {
            "method": self.version.method,
            "key": self.version.key,
            "secret": self.version.secret,
            "intended_for": self.version.intended_for,
        }
        if self.version.position is not None:
            body["position"] = self.version.position
        if self.use_path_param:
            path = f"/api/create-watermark/{self.document.id}"
        else:
            path = "/api/create-watermark"
            body["id"] = self.document.id
        return StepRequest("POST", path, json=body, user=self.user)
