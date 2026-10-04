"""Read the JSON reports in reports/ and organize them for the views.

Read-only: this module never runs tests or changes a report. It flattens every
report file into individual scenario runs, then groups them by group name and
scenario name, newest first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A run with no recognized status is shown as "error".
VALID_STATUS = {"passed", "failed", "error"}


@dataclass
class Run:
    """One scenario result taken from one report file."""

    group: str
    scenario: str
    status: str
    started_at: str | None
    finished_at: str | None
    source: str                      # report file name
    result: dict[str, Any]           # the full result object (steps, checks, ...)

    @property
    def when(self) -> str:
        """Best timestamp to sort and display by."""
        return self.finished_at or self.started_at or ""

    def to_summary(self) -> dict[str, Any]:
        checks = self.result.get("checks", [])
        return {
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "source": self.source,
            "checks_passed": sum(1 for c in checks if c.get("passed")),
            "checks_total": len(checks),
            "aborted": self.result.get("aborted"),
            "error": self.result.get("error"),
        }


@dataclass
class ScenarioHistory:
    scenario: str
    runs: list[Run] = field(default_factory=list)   # newest first

    @property
    def last(self) -> Run | None:
        return self.runs[0] if self.runs else None


class ReportStore:
    """Lazily reads and caches the reports directory, reloading when it changes."""

    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = Path(reports_dir)
        self._runs: list[Run] = []
        self._signature: tuple | None = None

    # ------------------------------------------------------------- loading

    def _dir_signature(self) -> tuple:
        """A cheap fingerprint of the directory: (name, size, mtime) per file."""
        if not self.reports_dir.is_dir():
            return ()
        entries = []
        for path in self.reports_dir.glob("*.json"):
            try:
                st = path.stat()
            except OSError:
                continue
            entries.append((path.name, st.st_size, st.st_mtime_ns))
        return tuple(sorted(entries))

    def _reload_if_changed(self) -> None:
        signature = self._dir_signature()
        if signature == self._signature:
            return
        self._runs = list(self._read_all())
        self._signature = signature

    def _read_all(self):
        for path in sorted(self.reports_dir.glob("*.json")):
            try:
                with path.open(encoding="utf-8") as fh:
                    report = json.load(fh)
            except (OSError, ValueError):
                continue  # skip a partial or unreadable report
            if not isinstance(report, dict):
                continue
            for result in report.get("results", []):
                if not isinstance(result, dict):
                    continue
                group = result.get("group")
                scenario = result.get("scenario")
                if not group or not scenario:
                    continue
                status = result.get("status")
                if status not in VALID_STATUS:
                    status = "error"
                yield Run(group=group, scenario=scenario, status=status,
                          started_at=result.get("started_at"),
                          finished_at=result.get("finished_at"),
                          source=path.name, result=result)

    # --------------------------------------------------------------- query

    def groups(self) -> list[str]:
        self._reload_if_changed()
        return sorted({run.group for run in self._runs})

    def history_for(self, group: str) -> list[ScenarioHistory]:
        """Per-scenario history for one group, scenarios sorted by name."""
        self._reload_if_changed()
        by_scenario: dict[str, list[Run]] = {}
        for run in self._runs:
            if run.group == group:
                by_scenario.setdefault(run.scenario, []).append(run)
        histories = []
        for scenario in sorted(by_scenario):
            runs = sorted(by_scenario[scenario], key=lambda r: r.when, reverse=True)
            histories.append(ScenarioHistory(scenario=scenario, runs=runs))
        return histories

    def run_detail(self, group: str, source: str, scenario: str) -> Run | None:
        """One specific run, identified by its report file and scenario."""
        self._reload_if_changed()
        for run in self._runs:
            if run.group == group and run.source == source and run.scenario == scenario:
                return run
        return None
