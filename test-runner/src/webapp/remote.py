"""Delegate certain reruns (RMAP) to a separate worker via a shared directory.

The webapp has no RMAP private key, so it does not run RMAP scenarios itself.
Instead it drops a small request file into a control directory that both this
container and the rmap-runner mount. The rmap-runner claims the request, runs
the scenario with the key it alone holds, writes the report to the shared
reports volume, and writes a result file the webapp reads back.

Only plain scenario/group requests cross this channel, never commands, so the
rmap-runner stays non-vulnerable.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

JOB_ID = re.compile(r"^[0-9a-f]{32}$")
REQ_SUFFIX = ".req.json"
TAKEN_SUFFIX = ".req.taken"
RES_SUFFIX = ".res.json"


class RemoteQueue:
    """Enqueues remote rerun requests and reads their results.

    Serializes to one outstanding remote job at a time (its own lane, separate
    from the webapp's in-process runner lock).
    """

    def __init__(self, control_dir: Path) -> None:
        self.control_dir = Path(control_dir)
        self._lock = threading.Lock()
        self._groups: dict[str, str] = {}      # job_id -> group (for authz)
        self._outstanding: str | None = None

    # ------------------------------------------------------------- paths

    def _req(self, job_id: str) -> Path:
        return self.control_dir / f"{job_id}{REQ_SUFFIX}"

    def _taken(self, job_id: str) -> Path:
        return self.control_dir / f"{job_id}{TAKEN_SUFFIX}"

    def _res(self, job_id: str) -> Path:
        return self.control_dir / f"{job_id}{RES_SUFFIX}"

    # ------------------------------------------------------------- enqueue

    def start(self, group: str, scenario: str) -> tuple[str | None, str | None]:
        """Enqueue a remote rerun. Returns (job_id, None) or (None, reason)."""
        self.control_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._outstanding and not self._res(self._outstanding).exists():
                return None, "an RMAP run is already in progress"
            job_id = uuid.uuid4().hex
            self._groups[job_id] = group
            self._outstanding = job_id

        payload = {"id": job_id, "group": group, "scenario": scenario,
                   "created": time.time()}
        tmp = self.control_dir / f".{job_id}.tmp"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self._req(job_id))        # atomic publish
        return job_id, None

    # -------------------------------------------------------------- status

    def status(self, job_id: str) -> dict[str, Any] | None:
        """Status of a remote job, or None if this id is not a remote job."""
        if not JOB_ID.match(job_id):
            return None
        res = self._res(job_id)
        if res.exists():
            try:
                data = json.loads(res.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            return {"id": job_id, "remote": True,
                    "state": data.get("state", "done"),
                    "status": data.get("status"),
                    "report": data.get("report"),
                    "error": data.get("error"),
                    "group": data.get("group") or self._groups.get(job_id, "")}
        if self._req(job_id).exists() or self._taken(job_id).exists() \
                or job_id in self._groups:
            return {"id": job_id, "remote": True, "state": "running", "status": None,
                    "report": None, "error": None,
                    "group": self._groups.get(job_id, "")}
        return None
