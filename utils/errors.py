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
import re
from typing import Any


# The API key travels to Zenrows as a query parameter, which means a transport
# failure carries it: `str(requests.ConnectionError)` embeds the full URL,
# apikey and all. Any message that interpolates an exception therefore leaks the
# credential into the workflow, its run log, and anywhere Dify surfaces a node
# error. Reproduced against a real connection failure before this was written.
_SECRET_PATTERNS = (
    re.compile(r"((?:apikey|api_key|api-key|token)=)[^&\s'\"]+", re.IGNORECASE),
    re.compile(
        r"((?:x-api-key|authorization)['\"]?\s*[:=]\s*['\"]?(?:bearer\s+|token\s+)?)[^,\s'\"}]+",
        re.IGNORECASE,
    ),
)


def redact(text: object) -> str:
    """Strip credentials out of anything on its way to a user-facing message."""
    out = str(text)
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(r"\1***", out)
    return out


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
# `ScraperApiException::isDomainScopedExtractRestriction` in the Zenrows app;
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
    detail = redact(error_detail(body) or (body[:240] if body else ""))
    # Where we have a better sentence than the API's, the raw prose only
    # repeats it at length -- Dify already prefixes every error with its own
    # boilerplate, so the useful part has to come early. Keep the code, which
    # is what support and the docs are searched by, and drop the duplicate.
    tag = f" ({code})" if code else ""

    if status in (401, 403):
        raise ZenrowsApiError(
            f"Update your Zenrows API key — the current one was rejected.{tag}",
            status=status,
            code=code,
        )

    if status == 402:
        if code == "AUTH010":
            # Two different failures share this code — say which one it is
            # rather than reporting a billing problem for either.
            if is_domain_scoped_extract_restriction(body):
                raise ZenrowsApiError(
                    "Switch this tool to autoparse, or ask Zenrows support to "
                    f"enable Extract for this domain.{tag}",
                    status=status,
                    code=code,
                )
            raise ZenrowsApiError(
                "Switch this tool to autoparse — Extract is not on your Zenrows "
                f"plan. Upgrading adds it.{tag}",
                status=status,
                code=code,
            )
        raise ZenrowsApiError(
            "Add credits or upgrade your plan — your Zenrows account is out "
            f"of credits.{tag}",
            status=status,
            code=code,
        )

    if status == 429:
        # Could be a quota, a concurrency cap, or the target site. Say so
        # rather than sending everyone to the billing page.
        raise ZenrowsApiError(
            "Wait and retry — rate limited (HTTP 429). This can be your "
            "account concurrency cap or the target site, not necessarily "
            f"exhausted credits.{tag}",
            status=status,
            code=code,
        )

    if code == "REQS001":
        raise ZenrowsApiError(
            "Zenrows does not allow scraping this domain. Retrying or changing "
            f"options will not help.{tag}",
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


def validate_url(url: str, param_name: str = "URL") -> str:
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


def resolve_stealth(
    stealth_param: object,
    js_render: bool,
    premium_proxy: bool,
    *,
    tool: str,
    implied_browser: str | None = None,
) -> bool:
    """Decide whether to send Adaptive Stealth Mode alongside explicit flags.

    `mode=auto` and the explicit `js_render` / `premium_proxy` flags are
    mutually exclusive at submit -- the API rejects a body carrying both. The
    tools default stealth on, so the three cases are not symmetric:

      * stealth left alone (None) + an explicit flag -> the user picked a tier
        deliberately. Honour it and drop stealth silently; erroring here would
        punish someone for setting js_render and never touching stealth.
      * stealth explicitly on + an explicit flag -> the user asked for two
        things that cannot both happen. Say so, naming both fields, rather
        than letting the API return something they cannot act on.
      * stealth off -> manual tier, nothing to resolve.

    `implied_browser` names an option the *tool* turned js_render on for --
    Screenshot, PDF output, Wait, and so on -- rather than one the user set.
    It still collides with stealth, but "unset Render JavaScript" is advice
    nobody can follow when the Render JavaScript toggle they can see is off.
    Passing the option's label lets the message name the thing they actually
    chose. Callers must pass the user's own js_render here, not the derived
    one, or the two cases cannot be told apart.

    Returns True when `mode=auto` should be sent.
    """
    explicit = js_render or premium_proxy
    if stealth_param is None:
        # A browser the tool switched on is just as incompatible with
        # `mode=auto` as one the user asked for, so it suppresses stealth too.
        return not (explicit or implied_browser)
    if as_bool(stealth_param):
        if explicit:
            chosen = " and ".join(
                n for n, v in (("Render JavaScript", js_render),
                               ("Premium proxy", premium_proxy)) if v
            )
            raise ToolParameterValidationError(
                f"Turn off Adaptive stealth, or unset {chosen} — Zenrows rejects "
                f"them together."
            )
        if implied_browser:
            raise ToolParameterValidationError(
                f"Turn off Adaptive stealth — {implied_browser} needs a browser, "
                f"and Zenrows rejects that together with adaptive stealth."
            )
        return True
    return False
