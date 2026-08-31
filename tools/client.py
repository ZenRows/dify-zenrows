"""HTTP layer for every Zenrows call the plugin makes.

Everything that talks to Zenrows goes through this module. That is
deliberate: the plugin calls the REST API directly because the `zenrows`
Python SDK has not been published with Fetch/Extract/Batch (PyPI still
serves 1.4.0), and Dify's marketplace validator rejects git/direct-URL
installs, so depending on the repo is not an option either. When a release
lands on PyPI, this file is the only one that changes.

Two clients, not one abstraction — the two APIs genuinely differ:

  Fetch / Extract   GET https://api.zenrows.com/v1/   auth: `apikey` query param
  Batch             https://async.api.zenrows.com/v1  auth: `X-API-Key` header

Attribution rides in the User-Agent, matching the CLI's `zenrows-cli/<ver>`.
"""

from __future__ import annotations

from typing import Any

import requests

from utils.errors import ToolInvokeError, raise_for_zenrows_error

FETCH_BASE = "https://api.zenrows.com/v1/"
BATCH_BASE = "https://async.api.zenrows.com/v1"
SUBSCRIPTION_URL = "https://api.zenrows.com/v1/subscriptions/self/details"

PLUGIN_VERSION = "0.0.1"
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


def fetch(api_key: str, url: str, params: dict[str, Any], *, action: str) -> requests.Response:
    """GET api.zenrows.com/v1/ with `apikey` and `url` plus scraper params.

    Returns the raw response: callers decide whether the body is text, JSON
    or binary, since `response_type` and `screenshot` change that.
    """
    query: dict[str, Any] = {"apikey": api_key, "url": url}
    for key, value in params.items():
        if value is None or value == "":
            continue
        # The API takes lowercase string booleans, not Python's True/False.
        query[key] = "true" if value is True else "false" if value is False else value
    return _request("GET", FETCH_BASE, params=query, action=action)


def fetch_raw(api_key: str, url: str, params: dict[str, Any], *, action: str):
    """Same call, but hands back (status, body_text, headers) without raising
    on 4xx — for the Extract fallback, which must inspect a 402 rather than
    have it turned into an exception."""
    query: dict[str, Any] = {"apikey": api_key, "url": url}
    for key, value in params.items():
        if value is None or value == "":
            continue
        query[key] = "true" if value is True else "false" if value is False else value
    try:
        response = requests.get(
            FETCH_BASE, params=query, headers=_headers(), timeout=DEFAULT_TIMEOUT
        )
    except requests.Timeout as exc:
        raise ToolInvokeError(f"Timed out after {DEFAULT_TIMEOUT}s while {action}.") from exc
    except requests.RequestException as exc:
        raise ToolInvokeError(f"Could not reach Zenrows while {action}: {exc}") from exc
    return response


# ----- Batch -----------------------------------------------------------


def batch(
    method: str,
    path: str,
    api_key: str,
    *,
    action: str,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> Any:
    """Call the Batch API and return parsed JSON. Different host and a header
    instead of a query param — see the module docstring."""
    response = _request(
        method,
        f"{BATCH_BASE}{path}",
        params=params,
        json_body=json_body,
        headers={"X-API-Key": api_key, "Accept": "application/json"},
        action=action,
    )
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise ToolInvokeError(
            f"Zenrows returned a non-JSON response while {action}."
        ) from exc


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
