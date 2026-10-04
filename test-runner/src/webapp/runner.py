"""Run one scenario on one group in the background, one at a time.

A single global lock serializes every rerun (the "one by one" rule), so a
rerun of any group blocks while another is in progress. The browser starts a
rerun, gets a job id, and polls its status until it is done.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("tatou.webapp")


@dataclass
class Job:
    id: str
    group: str
    scenario: str
    state: str = "running"           # running | done | failed
    status: str | None = None        # scenario status when done: passed/failed/error
    error: str | None = None         # message if the rerun itself failed
    report: str | None = None        # report file name produced
    started_at: float = 0.0
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "group": self.group, "scenario": self.scenario,
                "state": self.state, "status": self.status, "error": self.error,
                "report": self.report}


class Runner:
    """Serializes reruns and tracks their jobs.

    `run_fn(group, scenario)` does the actual work and returns (report_path,
    report_dict); it is injected so the web layer stays decoupled from the CLI.
    """

    def __init__(self, run_fn: Callable[[str, str], tuple[Path, dict]]) -> None:
        self._run_fn = run_fn
        self._run_lock = threading.Lock()       # only one rerun at a time
        self._state_lock = threading.Lock()     # guards _jobs and _active
        self._jobs: dict[str, Job] = {}
        self._active = False

    @property
    def busy(self) -> bool:
        with self._state_lock:
            return self._active

    def start(self, group: str, scenario: str) -> tuple[Job | None, str | None]:
        """Begin a rerun. Returns (job, None) or (None, reason) if one is running."""
        with self._state_lock:
            if self._active:
                return None, "another test run is already in progress"
            self._active = True
            job = Job(id=uuid.uuid4().hex, group=group, scenario=scenario,
                      started_at=_now())
            self._jobs[job.id] = job
        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return job, None

    def _run(self, job: Job) -> None:
        try:
            with self._run_lock:
                path, report = self._run_fn(job.group, job.scenario)
            job.report = Path(path).name
            results = report.get("results") or []
            group_errors = report.get("group_errors") or []
            if results:
                job.status = results[0].get("status")
                job.state = "done"
            elif group_errors:
                job.status = "error"
                job.error = group_errors[0].get("error")
                job.state = "done"
            else:
                job.status = "error"
                job.state = "done"
        except Exception as exc:
            log.exception("rerun of %s/%s failed", job.group, job.scenario)
            job.state = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = _now()
            with self._state_lock:
                self._active = False

    def job(self, job_id: str) -> Job | None:
        with self._state_lock:
            return self._jobs.get(job_id)


def _now() -> float:
    import time
    return time.monotonic()
