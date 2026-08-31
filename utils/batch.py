"""Shared Batch helpers.

Status vocabulary and the wait loop, kept out of the individual tools so
`batch_create` and `batch_status` agree on what "finished" means.
"""

from __future__ import annotations

import random
import time
from typing import Any

from tools.client import batch

# RunStatus, from the Batch API. `pending` is the idle state of an open job's
# initial run, not a failure — it is in-flight, not terminal.
IN_FLIGHT_STATUSES = {"running", "pending"}
TERMINAL_STATUSES = {"completed", "stopped", "failed"}

# Dify caps a tool invocation at 120s. Stop well before that so the tool can
# return a job id and a "still running" answer rather than being killed.
MAX_WAIT_SECONDS = 100
INITIAL_POLL_INTERVAL = 2.0
MAX_POLL_INTERVAL = 15.0


def get_job(api_key: str, job_id: str) -> dict[str, Any]:
    return batch("GET", f"/jobs/{job_id}", api_key, action="checking the batch job")


def run_summary(job: dict[str, Any]) -> dict[str, Any]:
    """Flatten the bits of a job a caller actually wants.

    `spend` is included deliberately: a batch costs real credits and the user
    should be able to see what it cost without opening the dashboard.
    """
    run = job.get("latest_run") or {}
    stats = run.get("stats") or {}
    summary: dict[str, Any] = {
        "job_id": job.get("job_id"),
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "finished": run.get("status") in TERMINAL_STATUSES,
        "total": stats.get("total"),
        "completed": stats.get("completed"),
        "successful": stats.get("successful"),
        "failed": stats.get("failed"),
    }
    if stats.get("failure_reasons"):
        summary["failure_reasons"] = stats["failure_reasons"]
    if stats.get("spend"):
        summary["spend"] = stats["spend"]
    return summary


def wait_for_run(api_key: str, job_id: str, *, max_seconds: int = MAX_WAIT_SECONDS) -> dict[str, Any]:
    """Poll until the run is terminal or the budget runs out.

    Jittered exponential backoff, matching the SDK's 2s -> 15s. Returns the
    last job payload either way: a timeout here is not an error, it means the
    caller should come back with `batch_status`.
    """
    deadline = time.monotonic() + max_seconds
    interval = INITIAL_POLL_INTERVAL
    job = get_job(api_key, job_id)

    while (job.get("latest_run") or {}).get("status") in IN_FLIGHT_STATUSES:
        if time.monotonic() + interval >= deadline:
            break
        # Jitter so many concurrent workflows do not poll in lockstep.
        time.sleep(interval * (0.8 + 0.4 * random.random()))
        interval = min(interval * 1.5, MAX_POLL_INTERVAL)
        job = get_job(api_key, job_id)

    return job
