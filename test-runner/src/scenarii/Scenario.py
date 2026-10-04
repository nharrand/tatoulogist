"""Scenario interface: one story tested against one group."""

from __future__ import annotations

import logging
import traceback
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from steps.Step import Step, StepResult, redact, utc_now

if TYPE_CHECKING:
    from model.Group import Group

log = logging.getLogger("tatou")


@dataclass
class Check:
    """Outcome of one verification made by a scenario."""

    description: str
    passed: bool
    details: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"description": self.description, "passed": self.passed,
                "details": redact(self.details)}


@dataclass
class ScenarioResult:
    """Steps run and checks made by one scenario on one group."""

    scenario: str
    group: str
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    steps: list[StepResult] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    aborted: str | None = None      # description of the failed require()
    error: str | None = None        # unexpected exception in the scenario code
    traceback: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and self.aborted is None \
            and all(c.passed for c in self.checks)

    @property
    def status(self) -> str:
        if self.error is not None:
            return "error"
        return "passed" if self.passed else "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "group": self.group,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "checks_passed": sum(c.passed for c in self.checks),
            "checks_failed": sum(not c.passed for c in self.checks),
            "aborted": self.aborted,
            "error": self.error,
            "traceback": self.traceback,
            "checks": [c.to_dict() for c in self.checks],
            "steps": [s.to_dict() for s in self.steps],
        }


class ScenarioAborted(Exception):
    """Raised by require() to stop a scenario after a blocking failure."""


class Scenario(ABC):
    """One story tested against one group.

    Subclasses implement execute(group). They run steps through self.step(),
    verify results with check(), require(), check_status() and check_fields(),
    and decide what to create or delete.

    Contract on local data: a scenario modifies the Group model (add or remove
    users, documents, versions) only after the server has confirmed the
    corresponding write. The CLI saves data.json after every scenario, so the
    model must reflect only what is really on the server, even if a later
    check fails.

    Every non-abstract subclass in the scenarii package is discovered by the
    CLI and selected by `name` (the class name by default). Subclasses with
    maintenance = True are only run when named explicitly.
    """

    #: Name used on the command line and in reports. Defaults to the class name.
    name: ClassVar[str | None] = None
    #: One-line description shown in listings.
    description: ClassVar[str] = ""
    #: Maintenance tools are not run by `--scenario all`; they must be named.
    maintenance: ClassVar[bool] = False

    def __init__(self) -> None:
        self._group: Group | None = None
        self._result: ScenarioResult | None = None

    @classmethod
    def scenario_name(cls) -> str:
        return cls.name or cls.__name__

    # ------------------------------------------------------------ lifecycle

    def run(self, group: Group) -> ScenarioResult:
        """Execute the scenario on `group` and return its result. Never raises."""
        self._group = group
        self._result = result = ScenarioResult(scenario=self.scenario_name(),
                                               group=group.name)
        log.info("[%s] %s: start", group.name, result.scenario)
        try:
            self.execute(group)
        except ScenarioAborted as exc:
            result.aborted = str(exc)
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            result.traceback = traceback.format_exc()
            log.debug("[%s] %s: exception\n%s", group.name, result.scenario,
                      result.traceback)
        finally:
            result.finished_at = utc_now()
            self._group = None
            self._result = None

        log.info("[%s] %s: %s (%d/%d checks passed)%s", group.name, result.scenario,
                 result.status.upper(), sum(c.passed for c in result.checks),
                 len(result.checks),
                 f" - aborted: {result.aborted}" if result.aborted else
                 f" - error: {result.error}" if result.error else "")
        return result

    @abstractmethod
    def execute(self, group: Group) -> None:
        """The story itself: run steps, make checks, update the model."""

    @property
    def result(self) -> ScenarioResult:
        if self._result is None:
            raise RuntimeError("result is only available while the scenario runs")
        return self._result

    # -------------------------------------------------------------- helpers

    def step(self, step: Step) -> StepResult:
        """Run a step on the current group and record its result."""
        if self._group is None:
            raise RuntimeError("step() can only be called while the scenario runs")
        result = step.run(self._group)
        self.result.steps.append(result)
        log.debug("[%s]   %s %s %s -> %s in %s%s", self._group.name, result.step,
                  result.method, result.url, result.status_code,
                  f"{result.elapsed:.3f}s" if result.elapsed is not None else "-",
                  f" ({result.error})" if result.error else "")
        return result

    def check(self, condition: bool, description: str, details: Any = None) -> bool:
        """Record a verification and return whether it passed."""
        passed = bool(condition)
        self.result.checks.append(Check(description, passed, details))
        level = logging.DEBUG if passed else logging.WARNING
        log.log(level, "[%s]   %s %s", self.result.group,
                "PASS" if passed else "FAIL", description)
        return passed

    def require(self, condition: bool, description: str, details: Any = None) -> None:
        """Like check(), but abort the scenario if it fails."""
        if not self.check(condition, description, details):
            raise ScenarioAborted(description)

    def check_status(self, result: StepResult, expected: int | Iterable[int] = 200,
                     description: str | None = None, *, required: bool = False) -> bool:
        """Verify the HTTP status code of a step result."""
        allowed = {expected} if isinstance(expected, int) else set(expected)
        description = description or (
            f"{result.step} returns {'/'.join(map(str, sorted(allowed)))}")
        details = {"status_code": result.status_code, "error": result.error,
                   "response": result.response_json}
        passed = result.status_code in allowed
        if required:
            self.require(passed, description, details)
            return True
        return self.check(passed, description, details)

    def check_fields(self, result: StepResult,
                     fields: Mapping[str, type | tuple[type, ...]],
                     description: str | None = None, *, data: Any = None,
                     required: bool = False) -> bool:
        """Verify that a JSON object has the given fields with the given types.

        By default the response JSON of `result` is checked. Pass `data` to
        check a nested object instead (e.g. one entry of a list).
        """
        obj = result.response_json if data is None else data
        description = description or f"{result.step} response has the expected fields"
        problems: list[str] = []
        if not isinstance(obj, dict):
            problems.append(f"expected a JSON object, got {type(obj).__name__}")
        else:
            for key, expected_type in fields.items():
                if key not in obj:
                    problems.append(f"missing '{key}'")
                elif not _is_instance(obj[key], expected_type):
                    problems.append(f"'{key}' is {type(obj[key]).__name__}")
        details = {"problems": problems} if problems else None
        if required:
            self.require(not problems, description, details)
            return True
        return self.check(not problems, description, details)


def _is_instance(value: Any, expected: type | tuple[type, ...]) -> bool:
    """isinstance() that does not accept a bool where an int is expected."""
    types = expected if isinstance(expected, tuple) else (expected,)
    if isinstance(value, bool) and bool not in types:
        return False
    return isinstance(value, types)
