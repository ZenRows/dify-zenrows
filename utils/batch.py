"""Shared Batch helpers, on top of `zenrows.ZenRowsBatchClient`.

Status vocabulary and the wait, kept out of the individual tools so
`batch_create` and `batch_status` agree on what "finished" means.

The SDK's models carry no field aliases, so `Job.model_dump(mode="json")`
nests exactly as the REST payload did (`latest_run.stats.*`). `run_summary`
therefore still takes a plain dict and its output shape is unchanged —
which matters, because those keys are the tools' declared output variables.
"""

from __future__ import annotations

from typing import Any

from zenrows import ZenRowsBatchClient
from zenrows.batch import BatchAPIError
from zenrows.batch._waiters import WaiterError, WaiterTimeout

from utils.errors import raise_for_zenrows_error

# RunStatus, from the Batch API: running, pending, completed, stopped,
# failed, deleted. `pending` is the idle state of an open job's initial run,
# not a failure — it is in-flight, not terminal.
IN_FLIGHT_STATUSES = {"running", "pending"}
TERMINAL_STATUSES = {"completed", "stopped", "failed", "deleted"}

# The SDK's own TERMINAL_RUN_STATUSES is {completed, stopped, deleted} — it
# omits `failed`. Waiting on that set would poll a run that has already
# failed until the timeout expires, so we pass our own set explicitly rather
# than taking the default.
WAIT_TARGET_STATUSES = frozenset(TERMINAL_STATUSES)

# Dify caps a tool invocation at 120s. Stop well before that so the tool can
# return a job id and a "still running" answer rather than being killed.
# (The SDK's own default is 300s, which would blow straight through the cap.)
MAX_WAIT_SECONDS = 100
INITIAL_POLL_INTERVAL = 2.0
MAX_POLL_INTERVAL = 15.0

# Per-request ceiling. Separate from MAX_WAIT_SECONDS, which budgets the
# whole poll loop.
REQUEST_TIMEOUT = 30.0


def batch_client(api_key: str) -> ZenRowsBatchClient:
    return ZenRowsBatchClient(api_key, timeout=REQUEST_TIMEOUT)


def reraise_batch_error(exc: BatchAPIError, action: str) -> None:
    """Translate an SDK error into the plugin's taxonomy.

    `raise_for_zenrows_error` owns every user-facing message, so the SDK's
    own string never reaches a workflow.
    """
    raise_for_zenrows_error(
        exc.status_code, exc.raw.decode("utf-8", errors="replace"), action=action
    )


def get_job(api_key: str, job_id: str) -> dict[str, Any]:
    try:
        with batch_client(api_key) as client:
            return client.get_job(job_id).data.model_dump(mode="json")
    except BatchAPIError as exc:
        reraise_batch_error(exc, "checking the batch job")
        raise  # unreachable; _reraise always raises


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
        # The API returns cost as a raw float, e.g. 0.030000000000000006.
        # Round it before a user ever sees it.
        spend = dict(stats["spend"])
        if isinstance(spend.get("cost"), (int, float)):
            spend["cost"] = round(spend["cost"], 6)
        summary["spend"] = spend
    return summary


def wait_for_run(api_key: str, job_id: str, *, max_seconds: int = MAX_WAIT_SECONDS) -> dict[str, Any]:
    """Wait until the run is terminal or the budget runs out.

    Returns the last job payload either way: running out of time is not an
    error here, it means the caller should come back with `batch_status`.
    The SDK's waiter does the jittered backoff we used to hand-roll.
    """
    try:
        with batch_client(api_key) as client:
            try:
                client.wait_for_run(
                    job_id,
                    target_statuses=set(WAIT_TARGET_STATUSES),
                    timeout=float(max_seconds),
                    poll_interval=INITIAL_POLL_INTERVAL,
                    max_poll_interval=MAX_POLL_INTERVAL,
                )
            except (WaiterTimeout, WaiterError):
                # Out of budget, or the waiter tripped its own failure
                # predicate. Either way the job payload below is the honest
                # answer and the caller decides what to do with it.
                pass
            return client.get_job(job_id).data.model_dump(mode="json")
    except BatchAPIError as exc:
        reraise_batch_error(exc, "checking the batch job")
        raise  # unreachable
