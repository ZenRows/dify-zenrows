"""Zenrows access for the plugin, on top of the `zenrows` SDK.

Fetch and Extract go through `zenrows.ZenRowsClient`, which returns a plain
`requests.Response` — so the error taxonomy in `utils/errors.py` still owns
how a non-2xx becomes a user-facing message.

Two things the SDK deliberately does not do for us:

  * It passes params straight through, so Python `True` would reach the wire
    as `"True"`. The API wants lowercase. `_normalise` handles that.
  * `ZenRowsClient` has no `user_agent` argument (unlike `ZenRowsBatchClient`),
    and supplying headers flips the gateway into `custom_headers` mode, which
    forwards them to the *target* site. So the plugin's attribution UA cannot
    ride on these calls today; it still does on Batch.

`verify_api_key` stays on plain `requests`: it reads billing state, which is
outside the SDK's surface.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
from zenrows import ZenRowsClient

from utils.errors import ToolInvokeError, raise_for_zenrows_error

SUBSCRIPTION_URL = "https://api.zenrows.com/v1/subscriptions/self/details"

PLUGIN_VERSION = "0.1.0"
USER_AGENT = f"zenrows-dify-plugin/{PLUGIN_VERSION}"

# Dify caps a tool invocation at 120s (MAX_REQUEST_TIMEOUT in main.py), so no
# single call may sit anywhere near that or the whole tool times out instead
# of returning a usable error.
DEFAULT_TIMEOUT = 90


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if extra:
        headers.update(extra)
    return headers


def _request(
    method: str,
    url: str,
    *,
    action: str,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> requests.Response:
    """One call, with transport failures and API errors both surfaced as
    ToolInvokeError so a tool never leaks a raw traceback into a workflow."""
    try:
        response = requests.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=_headers(headers),
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise ToolInvokeError(
            f"Timed out after {timeout}s while {action}."
        ) from exc
    except requests.RequestException as exc:
        raise ToolInvokeError(f"Could not reach Zenrows while {action}: {exc}") from exc

    raise_for_zenrows_error(response.status_code, response.text, action=action)
    return response


# ----- Fetch / Extract -------------------------------------------------


def _normalise(params: dict[str, Any]) -> dict[str, Any]:
    """Drop unset values and lowercase booleans — the API rejects "True"."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or value == "":
            continue
        out[key] = "true" if value is True else "false" if value is False else value
    return out


def _sdk_call(api_key: str, url: str, params: dict[str, Any], *, action: str) -> requests.Response:
    try:
        return ZenRowsClient(api_key).fetch(
            url, params=_normalise(params), timeout=DEFAULT_TIMEOUT
        )
    except requests.Timeout as exc:
        raise ToolInvokeError(f"Timed out after {DEFAULT_TIMEOUT}s while {action}.") from exc
    except requests.RequestException as exc:
        raise ToolInvokeError(f"Could not reach Zenrows while {action}: {exc}") from exc


def fetch(api_key: str, url: str, params: dict[str, Any], *, action: str) -> requests.Response:
    """A scrape, with non-2xx raised through the plugin's error taxonomy."""
    response = _sdk_call(api_key, url, params, action=action)
    raise_for_zenrows_error(response.status_code, response.text, action=action)
    return response


def fetch_raw(api_key: str, url: str, params: dict[str, Any], *, action: str):
    """Same call without raising, so Extract can inspect a 402 (AUTH010)
    rather than have it turned into an exception."""
    return _sdk_call(api_key, url, params, action=action)


# ----- Credential validation -------------------------------------------


def verify_api_key(api_key: str) -> dict[str, Any]:
    """Check a key without spending anything.

    `subscriptions/self/details` returns the account's plan and usage. It is
    a read of billing state and does not consume credits — verified by
    calling it repeatedly against a live account with `usage_credits`
    unchanged. That matters: this runs every time a user saves their
    credentials, so it must not cost them a scrape.
    """
    response = _request(
        "GET",
        SUBSCRIPTION_URL,
        params={"apikey": api_key},
        headers={"Accept": "application/json"},
        action="verifying the API key",
        timeout=30,
    )
    try:
        return response.json()
    except ValueError as exc:
        raise ToolInvokeError(
            "Zenrows returned an unexpected response while verifying the API key."
        ) from exc

# ----- Batch result bodies ---------------------------------------------

# The SDK's bulk-download defaults are 100_000 files at 50 MiB each, sized for
# a CLI streaming to disk. A Dify tool returns into a workflow variable and has
# to finish inside the 120s invocation ceiling, so it needs its own limits.
DEFAULT_MAX_RESULTS = 25
MAX_RESULTS_CEILING = 200
DEFAULT_MAX_BYTES_PER_BODY = 1024 * 1024  # 1 MiB

# Bodies are independent GETs against object storage, so they parallelise
# cleanly. Sequentially, 25 bodies measured ~13.5s; at the 200 ceiling that
# alone would exceed Dify's 120s invocation limit before any API calls. Eight
# workers keeps the wall time roughly flat as the count grows.
RESULT_FETCH_WORKERS = 8


def fetch_result_body(run, task: Any, *, max_bytes: int = DEFAULT_MAX_BYTES_PER_BODY) -> str | None:
    """Download one task's body via the SDK and return it as text.

    `RunRef.download_task_to_memory` GETs the presigned `result_url` directly -- no
    auth header, no API content endpoint in the loop. It returns the whole
    body with no size cap of its own, so the cap is applied here: the old
    hand-rolled version streamed and bailed early, this one downloads then
    discards. A body large enough to matter is rare and the ceiling is 1 MiB,
    so the difference is transfer, not memory pressure.

    Returns None when the body is missing, oversized, or will not download,
    so one bad page cannot fail the whole batch.
    """
    if task is None:
        return None
    try:
        body = run.download_task_to_memory(task)
    except Exception:
        # A body that will not download should not fail the whole batch --
        # the row still reports its status and URL.
        return None
    if body is None or len(body) > max_bytes:
        return None
    return body.decode("utf-8", errors="replace")


def fetch_result_bodies(
    run, tasks: list[Any], *, max_bytes: int = DEFAULT_MAX_BYTES_PER_BODY
) -> list[str | None]:
    """Fetch many result bodies in parallel, preserving input order.

    `run` is a RunRef; the underlying httpx client is safe to share across
    the pool, and every body is an independent presigned GET.
    """
    if not tasks:
        return []
    workers = min(RESULT_FETCH_WORKERS, len(tasks))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda t: fetch_result_body(run, t, max_bytes=max_bytes), tasks))
