"""Zenrows API error handling.

One place that turns a Zenrows HTTP response into an exception Dify can
show a user. The taxonomy mirrors the Zenrows CLI's `src/core/http.ts`,
which is the reference implementation for these codes.

The distinctions that matter:

* 402 is two different failures. `AUTH010` on an Extract request means the
  target domain is not enrolled in the Extract beta — recoverable, the
  caller retries with `autoparse`. Any other 402 (e.g. `AUTH004`) means the
  account is out of credits, which is not recoverable and must not trigger
  a retry: a blind fallback would spend a second billable call on an
  account that has none.
* 429 is not always a quota problem. It is also returned for an account
  concurrency cap and for target-site rate limiting, so the message must
  not tell users to buy credits they may not need.
* `REQS001` is permanent. Zenrows refuses that domain at the policy layer;
  js_render, premium_proxy and retries all fail identically.
"""

from __future__ import annotations

import json
from typing import Any


class ToolInvokeError(Exception):
    """A call failed for a reason the user can act on."""


class ToolParameterValidationError(Exception):
    """A tool parameter was missing or malformed."""


# Re-raise these untouched. Without this they get re-wrapped by the generic
# handler as "Unexpected error while ...", which buries the useful message.
PASSTHROUGH_ERRORS = (ToolInvokeError, ToolParameterValidationError)


class ZenrowsApiError(ToolInvokeError):
    """A non-2xx from the Zenrows API, with the parsed error envelope."""

    def __init__(self, message: str, *, status: int, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def parse_error_envelope(body: str) -> dict[str, Any]:
    """Zenrows errors are JSON: {code, title, detail, status, type}.

    Returns {} for anything that is not that shape — an HTML error page from
    an intermediary, an empty body, a truncated response.
    """
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def error_code(body: str) -> str | None:
    """The `code` field, upper-cased. None when absent or unparseable."""
    code = parse_error_envelope(body).get("code")
    return code.upper() if isinstance(code, str) else None


def error_detail(body: str) -> str | None:
    """A one-line human detail, e.g. `(AUTH003) The apikey sent is not valid.`

    Prefers `detail` over `title`: `title` is a short label that already ends
    with the code ("Invalid apikey provided (AUTH003)"), while `detail` is the
    sentence that actually tells the user what to do. The code is only
    prepended when the text does not already carry it, so the message never
    reads "(AUTH003) ... (AUTH003)".
    """
    envelope = parse_error_envelope(body)
    label = envelope.get("detail") or envelope.get("title")
    if not isinstance(label, str) or not label:
        return None
    label = label.strip()
    code = envelope.get("code")
    if not isinstance(code, str) or code in label:
        return label
    return f"({code}) {label}"


# AUTH010 is overloaded upstream: it means both "your plan does not include
# Extract" and "Extract is not enabled for this domain yet". Only the second is
# recoverable by falling back to autoparse — falling back on a plan restriction
# would silently downgrade every call and never tell the user to upgrade.
#
# The two are only distinguishable by the `detail` text. This mirrors
# `ScraperApiException::isDomainScopedExtractRestriction` in the ZenRows app;
# note that "private beta" alone is not sufficient, because "Extract is in
# private beta and is not included in your plan" is a plan restriction.
_DOMAIN_SCOPED_PHRASES = (
    "not enabled for the requested domain",
    "not enabled for this domain",
)


def is_domain_scoped_extract_restriction(body: str) -> bool:
    """True when an AUTH010 detail is about the domain, not the plan."""
    text = (error_detail(body) or "").lower()
    if not text:
        return False
    if any(phrase in text for phrase in _DOMAIN_SCOPED_PHRASES):
        return True
    return "private beta" in text and "domain" in text


def is_extract_domain_not_enabled(status: int, body: str) -> bool:
    """True only for the recoverable 402 — the Extract beta gate on a domain.

    Callers use this to decide whether falling back to `autoparse` is safe.
    Deliberately narrow: a 402 without `AUTH010` is a credits failure, and an
    AUTH010 that is not domain-scoped is a plan restriction the user must see.
    """
    if status != 402 or error_code(body) != "AUTH010":
        return False
    return is_domain_scoped_extract_restriction(body)


def raise_for_zenrows_error(status: int, body: str, *, action: str) -> None:
    """Raise the right error for a non-2xx Zenrows response.

    `action` names what was being attempted, e.g. "fetching the page".
    """
    if 200 <= status < 300:
        return

    code = error_code(body)
    detail = error_detail(body) or (body[:240] if body else "")

    if status in (401, 403):
        raise ZenrowsApiError(
            "Zenrows rejected the API key. Check the key in your Zenrows "
            f"dashboard and update the plugin credentials. {detail}".strip(),
            status=status,
            code=code,
        )

    if status == 402:
        if code == "AUTH010":
            # Two different failures share this code — say which one it is
            # rather than reporting a billing problem for either.
            if is_domain_scoped_extract_restriction(body):
                raise ZenrowsApiError(
                    "Extract is not enabled for this domain yet. Retry with "
                    f"autoparse, or contact Zenrows support. {detail}".strip(),
                    status=status,
                    code=code,
                )
            raise ZenrowsApiError(
                "Extract is not available on your Zenrows plan. Upgrade to "
                f"use it, or switch this tool to autoparse. {detail}".strip(),
                status=status,
                code=code,
            )
        raise ZenrowsApiError(
            "Your Zenrows account is out of credits. Add credits or upgrade "
            f"your plan, then retry. {detail}".strip(),
            status=status,
            code=code,
        )

    if status == 429:
        # Could be a quota, a concurrency cap, or the target site. Say so
        # rather than sending everyone to the billing page.
        raise ZenrowsApiError(
            "Rate limited by Zenrows (HTTP 429). This may be an account "
            "concurrency cap or the target site rate limiting, not "
            f"necessarily exhausted credits. Wait and retry. {detail}".strip(),
            status=status,
            code=code,
        )

    if code == "REQS001":
        raise ZenrowsApiError(
            "Zenrows does not allow scraping this domain. This is permanent — "
            f"retrying or changing options will not help. {detail}".strip(),
            status=status,
            code=code,
        )

    raise ZenrowsApiError(
        f"Zenrows returned HTTP {status} while {action}. {detail}".strip(),
        status=status,
        code=code,
    )


def require_param(params: dict[str, Any], key: str, message: str | None = None) -> Any:
    value = params.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ToolParameterValidationError(message or f"{key} is a required parameter.")
    return value


def validate_url(url: str, param_name: str = "url") -> str:
    if not isinstance(url, str) or not url.strip():
        raise ToolParameterValidationError(f"{param_name} must be a non-empty string.")
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ToolParameterValidationError(
            f"{param_name} must start with http:// or https://."
        )
    return url

def as_bool(value: Any) -> bool:
    """Coerce a Dify parameter to a real bool.

    A `boolean` param can arrive as a Python bool, or as a string ("true",
    "false", "1", "0") depending on whether the value came from the form, a
    workflow variable, or an LLM. `bool("0")` is True in Python, so a naive
    cast silently inverts every toggle a user left off — which would, among
    other things, turn on premium_proxy and bill them for it.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)
