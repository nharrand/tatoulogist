#!/usr/bin/env python3
"""Periodic backup of the Tatou test-bench data.

Run from cron. It does two things each cycle:

  1. Appends a line to a manifest recording which group each report concerns,
     so operators can see which groups have been active between backups.
  2. Archives the data directory to a timestamped tar.gz.

The data directory is mounted read-only; backups are written to /backups.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

REPORTS_DIR = Path("/reports")
DATA_DIR = Path("/data")
BACKUP_DIR = Path("/backups")


def group_of(report: Path) -> str:
    """The group a report concerns, used to tag it in the manifest."""
    try:
        data = json.loads(report.read_text())
    except (OSError, ValueError):
        return "unreadable"
    results = data.get("results") or []
    if results and isinstance(results[0], dict):
        return str(results[0].get("group", "unknown"))
    return "empty"


def main() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    manifest = BACKUP_DIR / "manifest.txt"

    # Record every report's group in the manifest.
    for report in sorted(REPORTS_DIR.glob("*.json")):
        group = group_of(report)
        subprocess.run(f"echo '{stamp} {report.name} {group}' >> {manifest}",
                       shell=True, check=False)

    # Archive the data directory.
    subprocess.run(f"tar czf {BACKUP_DIR}/data_{stamp}.tar.gz -C {DATA_DIR} .",
                   shell=True, check=False)
    print(f"[{stamp}] backup complete", flush=True)


if __name__ == "__main__":
    main()
