"""The watermark round trip of WatermarkLifecycle, once per watermarking method."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from scenarii.Scenario import ScenarioAborted
from scenarii.WatermarkLifecycle import WatermarkLifecycle

if TYPE_CHECKING:
    from model.Group import Group

log = logging.getLogger("tatou")


class AllMethodsLifecycle(WatermarkLifecycle):
    """Take an existing user (or create one), log in, upload one PDF from
    data/pdfs and check that it is listed. Then, for every method returned by
    get-watermarking-methods, run the WatermarkLifecycle round trip on that
    document:

        create watermark -> list versions -> get version
        -> re-upload -> read watermark -> delete the re-uploaded copy

    A failure in one method's round trip is recorded and the next method is
    still tested. Every check made during a round trip is prefixed with the
    method name, e.g. "[toy-eof] read-watermark returns the embedded secret".
    A final check lists the methods that failed.
    """

    description = "Run the watermark round trip of WatermarkLifecycle with every method"

    #: Method names to leave out of the run.
    SKIP: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        super().__init__()
        self._method: str | None = None

    def execute(self, group: Group) -> None:
        user = self.get_user(group)
        self.login(user)
        document = self.upload_source(group, user)
        self.check_document_listed(user, document)

        methods = [m for m in dict.fromkeys(self.list_methods()) if m not in self.SKIP]
        self.require(bool(methods), "at least one watermarking method to test",
                     {"skipped": sorted(self.SKIP)})

        failed: list[str] = []
        for method in methods:
            log.info("[%s]   testing method %r", group.name, method)
            checks_before = len(self.result.checks)
            self._method = method
            try:
                self.round_trip(group, user, document, method)
            except ScenarioAborted as exc:
                log.info("[%s]   %r stopped: %s", group.name, method, exc)
            finally:
                self._method = None
            if any(not c.passed for c in self.result.checks[checks_before:]):
                failed.append(method)

        self.check(not failed, "every watermarking method completes the round trip",
                   {"tested": methods, "failed": failed})

    def check(self, condition: bool, description: str, details: Any = None) -> bool:
        """Prefix checks made during a round trip with the method being tested."""
        if self._method is not None:
            description = f"[{self._method}] {description}"
        return super().check(condition, description, details)
