"""Check that the server answers /healthz as specified."""

from __future__ import annotations

from typing import TYPE_CHECKING

from scenarii.Scenario import Scenario
from steps.Healthz import Healthz

if TYPE_CHECKING:
    from model.Group import Group


class HealthCheck(Scenario):
    """GET /healthz without authentication must answer 200 with a JSON
    object containing a "message" string."""

    description = "Check that /healthz answers without authentication with a message"

    def execute(self, group: Group) -> None:
        result = self.step(Healthz())
        self.require(result.responded, "the server answers /healthz",
                     {"error": result.error})
        self.check_status(result, 200, "healthz succeeds without authentication")
        self.check_fields(result, {"message": str}, "healthz returns a message string")
