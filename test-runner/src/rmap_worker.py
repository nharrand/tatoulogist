"""Run RMAP reruns requested through the shared control directory.

This process is the only place the RMAP private key and passphrase exist. It
runs no web server and builds no shell commands, so it is not exposed to the
webapp's template injection or the backup's command injection. It executes only
scenarios on an allowlist (default: RmapHandshake), on groups from groups.csv,
through the same CLI.run_one the test bench uses. Nothing from a request becomes
a shell command.

    python -m rmap_worker        (or the tatou-rmap-worker console script)

Environment:
  TATOU_DATA_DIR          data directory (default: data)
  TATOU_REPORTS_DIR       reports directory (default: reports)
  TATOU_CONTROL_DIR       shared control directory (default: control)
  TATOU_REMOTE_SCENARIOS  comma-separated allowlist (default: RmapHandshake)
  TATOU_TIMEOUT           HTTP timeout, seconds (default: 5)
  TATOU_RMAP_IDENTITY     identity sent in RMAP message 1
  TATOU_KEY_PASSPHRASE    passphrase for the RMAP private key, if protected
  TATOU_POLL_INTERVAL     seconds between scans (default: 1)
  TATOU_RESULT_TTL        seconds to keep result files (default: 3600)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import CLI

log = logging.getLogger("tatou.rmap")

JOB_ID = re.compile(r"^[0-9a-f]{32}$")
REQ_SUFFIX = ".req.json"
TAKEN_SUFFIX = ".req.taken"
RES_SUFFIX = ".res.json"


def _write_result(control_dir: Path, job_id: str, result: dict) -> None:
    tmp = control_dir / f".{job_id}.res.tmp"
    tmp.write_text(json.dumps(result), encoding="utf-8")
    tmp.replace(control_dir / f"{job_id}{RES_SUFFIX}")


def process(job_id: str, taken: Path, *, data_dir: Path, reports_dir: Path,
            control_dir: Path, timeout: float, identity: str | None,
            passphrase: str | None, allow: set[str]) -> None:
    result = {"id": job_id, "state": "failed", "status": None, "report": None,
              "error": None, "group": None}
    try:
        job = json.loads(taken.read_text(encoding="utf-8"))
        group = str(job.get("group", ""))
        scenario = str(job.get("scenario", ""))
        result["group"] = group
        if scenario not in allow:
            result["error"] = f"scenario not allowed on the RMAP runner: {scenario!r}"
            result["state"] = "failed"
        else:
            log.info("running %s on %s", scenario, group)
            path, report = CLI.run_one(data_dir, group, scenario, timeout=timeout,
                                       rmap_identity=identity, key_passphrase=passphrase,
                                       reports_dir=reports_dir)
            result["report"] = Path(path).name
            results = report.get("results") or []
            group_errors = report.get("group_errors") or []
            result["state"] = "done"
            if results:
                result["status"] = results[0].get("status")
            elif group_errors:
                result["status"] = "error"
                result["error"] = group_errors[0].get("error")
            else:
                result["status"] = "error"
    except CLI.UsageError as exc:
        result["error"] = str(exc)
    except Exception as exc:
        log.exception("job %s failed", job_id)
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _write_result(control_dir, job_id, result)
        try:
            taken.unlink()
        except OSError:
            pass
        log.info("job %s -> %s (%s)", job_id, result["state"], result["status"])


def sweep(control_dir: Path, ttl: float) -> None:
    """Delete result and stale claim files older than ttl seconds."""
    cutoff = time.time() - ttl
    for pattern in (f"*{RES_SUFFIX}", f"*{TAKEN_SUFFIX}"):
        for path in control_dir.glob(pattern):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
                        datefmt="%H:%M:%S")
    data_dir = Path(os.environ.get("TATOU_DATA_DIR", "data"))
    reports_dir = Path(os.environ.get("TATOU_REPORTS_DIR", "reports"))
    control_dir = Path(os.environ.get("TATOU_CONTROL_DIR", "control"))
    timeout = float(os.environ.get("TATOU_TIMEOUT", "5"))
    identity = os.environ.get("TATOU_RMAP_IDENTITY") or None
    passphrase = os.environ.get("TATOU_KEY_PASSPHRASE") or None
    allow = {s.strip() for s in
             os.environ.get("TATOU_REMOTE_SCENARIOS", "RmapHandshake").split(",")
             if s.strip()}
    interval = float(os.environ.get("TATOU_POLL_INTERVAL", "1"))
    ttl = float(os.environ.get("TATOU_RESULT_TTL", "3600"))

    control_dir.mkdir(parents=True, exist_ok=True)
    log.info("rmap worker watching %s; allowlist=%s; identity=%s",
             control_dir, sorted(allow), identity)

    last_sweep = 0.0
    while True:
        for req in sorted(control_dir.glob(f"*{REQ_SUFFIX}")):
            job_id = req.name[:-len(REQ_SUFFIX)]
            if not JOB_ID.match(job_id):
                continue
            taken = control_dir / f"{job_id}{TAKEN_SUFFIX}"
            try:
                req.rename(taken)          # atomic claim
            except OSError:
                continue                   # already claimed or vanished
            process(job_id, taken, data_dir=data_dir, reports_dir=reports_dir,
                    control_dir=control_dir, timeout=timeout, identity=identity,
                    passphrase=passphrase, allow=allow)

        now = time.time()
        if now - last_sweep > 60:
            sweep(control_dir, ttl)
            last_sweep = now
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
