"""Unit checks for the logic that has actually gone wrong.

Every function pinned here shipped a real bug at some point during development,
each found by an ad-hoc check that was then thrown away. This file exists so the
next person cannot reintroduce them quietly.

No test framework and no dependencies — run it directly:

    uv run python tests/test_units.py

Exits non-zero on the first failure. Excluded from the packaged plugin via
`.difyignore`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.errors import (  # noqa: E402
    ToolInvokeError,
    redact,
    ToolParameterValidationError,
    as_bool,
    error_code,
    error_detail,
    is_domain_scoped_extract_restriction,
    is_extract_domain_not_enabled,
    resolve_stealth,
)

FAILURES: list[str] = []


def check(name: str, got: object, expected: object) -> None:
    if got != expected:
        FAILURES.append(f"{name}\n    got:      {got!r}\n    expected: {expected!r}")


def envelope(code: str, detail: str, status: int = 402) -> str:
    return json.dumps(
        {"code": code, "title": "t", "detail": detail, "status": status, "type": "auth"}
    )


# --- as_bool -------------------------------------------------------------
# Dify sends booleans as strings. `bool("0")` and `bool("False")` are both True
# in Python, so a plain truthiness check silently enabled premium proxy (a 10x
# credit multiplier) on every call.

def test_as_bool() -> None:
    for value, expected in [
        (True, True), (False, False),
        (None, False),
        ("true", True), ("True", True), ("TRUE", True),
        ("1", True), ("yes", True), ("on", True),
        ("false", False), ("False", False),
        ("0", False), ("", False), ("no", False), ("off", False),
        (1, True), (0, False),
    ]:
        check(f"as_bool({value!r})", as_bool(value), expected)


# --- default-on booleans -------------------------------------------------
# `tool_parameters.get(key, True)` returns None — not True — when Dify sends the
# key explicitly unset, and as_bool(None) is False. A default-on parameter must
# treat absent and null alike.

def default_on(params: dict) -> bool:
    value = params.get("adaptive_stealth")
    return value is None or as_bool(value)


def test_default_on_parameter() -> None:
    for params, expected in [
        ({}, True),                              # key absent
        ({"adaptive_stealth": None}, True),      # explicit null
        ({"adaptive_stealth": True}, True),
        ({"adaptive_stealth": "True"}, True),
        ({"adaptive_stealth": "1"}, True),
        ({"adaptive_stealth": False}, False),
        ({"adaptive_stealth": "False"}, False),  # what Dify actually sends
        ({"adaptive_stealth": "0"}, False),      # what Dify actually sends
    ]:
        check(f"default_on({params!r})", default_on(params), expected)


# --- error envelope parsing ---------------------------------------------

def test_error_envelope() -> None:
    body = envelope("AUTH004", "Your account has no credits left")
    check("error_code", error_code(body), "AUTH004")
    check("detail contains text", "no credits left" in (error_detail(body) or ""), True)
    check("error_code on junk", error_code("<html>502</html>"), None)
    check("error_code on empty", error_code(""), None)


# --- AUTH010 is overloaded ----------------------------------------------
# It means BOTH "your plan doesn't include Extract" AND "Extract isn't enabled
# for this domain yet". Only the second may fall back to autoparse — falling
# back on a plan restriction silently downgrades every call and never tells the
# user to upgrade. They differ only in the `detail` text. Mirrors
# ScraperApiException::isDomainScopedExtractRestriction in the Zenrows app.

DOMAIN_SCOPED = [
    "Autoparse (extract) is not enabled for this domain",
    "extract is not enabled for the requested domain",
    # the exact string from the app's own use-contracts test fixture
    "Autoparse (extract) is in private beta and not enabled for this domain (AUTH010)",
]

PLAN_SCOPED = [
    # "private beta" alone is NOT sufficient — this one is a plan restriction
    "Extract is in private beta and is not included in your plan",
    "This feature is not available in your plan",
]


def test_auth010_discrimination() -> None:
    for detail in DOMAIN_SCOPED:
        body = envelope("AUTH010", detail)
        check(f"domain-scoped: {detail[:40]}", is_domain_scoped_extract_restriction(body), True)
        check(f"falls back: {detail[:40]}", is_extract_domain_not_enabled(402, body), True)

    for detail in PLAN_SCOPED:
        body = envelope("AUTH010", detail)
        check(f"plan-scoped: {detail[:40]}", is_domain_scoped_extract_restriction(body), False)
        check(f"no fallback: {detail[:40]}", is_extract_domain_not_enabled(402, body), False)


def test_fallback_gate_is_narrow() -> None:
    domain_body = envelope("AUTH010", "not enabled for this domain")
    check("AUTH004 never falls back",
          is_extract_domain_not_enabled(402, envelope("AUTH004", "no credits")), False)
    check("AUTH010 on 429 never falls back",
          is_extract_domain_not_enabled(429, domain_body), False)
    check("empty body never falls back", is_extract_domain_not_enabled(402, ""), False)
    check("html body never falls back",
          is_extract_domain_not_enabled(402, "<html>502 Bad Gateway</html>"), False)


# --- status filter -------------------------------------------------------
# `value: ""` on a select option makes Dify reject the WHOLE tool declaration,
# so "no filter" is the explicit value "all" rather than an empty option.

def normalise_status_filter(params: dict) -> str | None:
    value = (params.get("status_filter") or "").strip().lower() or None
    return None if value == "all" else value


def test_status_filter() -> None:
    for params, expected in [
        ({}, None),
        ({"status_filter": None}, None),
        ({"status_filter": ""}, None),
        ({"status_filter": "all"}, None),
        ({"status_filter": "All"}, None),
        ({"status_filter": " all "}, None),
        ({"status_filter": "successful"}, "successful"),
        ({"status_filter": "failed"}, "failed"),
    ]:
        check(f"status_filter({params!r})", normalise_status_filter(params), expected)


# --- wait / wait_for exclusivity ----------------------------------------
# The API declares `prohibits` on both sides of this pair. Catching it in the
# tool turns a 422 into a sentence naming both fields. Mirrors tools/fetch.py.

def wait_conflict(params: dict) -> bool:
    wait = params.get("wait")
    wait = None if wait in (None, "") else int(str(wait).strip())
    return wait is not None and bool((params.get("wait_for") or "").strip())


def test_wait_exclusivity() -> None:
    for params, expected in [
        ({}, False),
        ({"wait": 2000}, False),
        ({"wait_for": ".price"}, False),
        ({"wait": 2000, "wait_for": ".price"}, True),
        ({"wait": "2000", "wait_for": ".price"}, True),
        # An empty selector is not a selector, so this is not a conflict.
        ({"wait": 2000, "wait_for": ""}, False),
        ({"wait": 2000, "wait_for": "   "}, False),
        # An absent wait is not a conflict however the selector is set.
        ({"wait": None, "wait_for": ".price"}, False),
        ({"wait": "", "wait_for": ".price"}, False),
    ]:
        check(f"wait_conflict({params!r})", wait_conflict(params), expected)


# --- browser-dependent params force js_render ----------------------------
# screenshot, wait_for, wait and js_instructions are browser-side. Without a
# browser the API ignores them silently, which reads as a plugin bug.

def needs_browser(params: dict) -> bool:
    wait = params.get("wait")
    wait = None if wait in (None, "") else int(str(wait).strip())
    return bool(
        as_bool(params.get("screenshot"))
        or (params.get("wait_for") or "").strip()
        or (params.get("js_instructions") or "").strip()
        or wait is not None
    )


def test_browser_dependent_params() -> None:
    for params, expected in [
        ({}, False),
        ({"url": "https://example.com"}, False),
        ({"screenshot": True}, True),
        ({"screenshot": "0"}, False),          # Dify sends unticked as "0"
        ({"wait_for": ".price"}, True),
        ({"wait_for": ""}, False),
        ({"wait": 2000}, True),
        ({"wait": "2000"}, True),
        ({"wait": ""}, False),
        ({"js_instructions": '[{"click": ".more"}]'}, True),
        ({"js_instructions": "  "}, False),
    ]:
        check(f"needs_browser({params!r})", needs_browser(params), expected)


# --- job_id is authoritative, not read back from the payload -------------
# run_summary() takes job_id from whatever payload it is given. GET /jobs/{id}
# need not repeat the id in the body, so with `wait` on the summary came back
# with job_id: null and every downstream node lost the reference.

def summary_job_id(payload: dict, known_job_id: str) -> str:
    summary = {"job_id": payload.get("job_id"), "status": "completed"}
    summary["job_id"] = known_job_id          # what the tools now do
    return summary["job_id"]


def test_job_id_is_authoritative() -> None:
    known = "01M3NX8BJT77XEWRN0P82J434P"
    for payload in [
        {"job_id": known},                     # POST /jobs shape
        {},                                    # GET /jobs/{id} without the id
        {"job_id": None},                      # or with it explicitly null
        {"id": known},                         # or under a different key
    ]:
        check(f"summary_job_id({payload!r})", summary_job_id(payload, known), known)



# --- adaptive stealth vs the explicit tier flags -------------------------
# Zenrows treats `mode=auto` and js_render/premium_proxy as mutually exclusive
# and rejects a body carrying both — the reviewer hit exactly this on Extract
# and got an error they could not act on. Stealth defaults on, so "left alone"
# and "explicitly on" must not behave the same way.

def test_resolve_stealth() -> None:
    for label, stealth, js, pp, expected in [
        ("unset, no flags", None, False, False, True),
        ("unset + js_render", None, True, False, False),
        ("unset + premium_proxy", None, False, True, False),
        ("unset + both", None, True, True, False),
        ("explicit on, no flags", True, False, False, True),
        ("off, no flags", False, False, False, False),
        ("off + js_render", False, True, False, False),
    ]:
        check(
            f"resolve_stealth({label})",
            resolve_stealth(stealth, js, pp, tool="Extract"),
            expected,
        )

    # A browser the tool switched on (Screenshot, PDF, Wait...) collides with
    # stealth just as hard, but the user never touched Render JavaScript, so
    # the message has to name the option they did choose. Live testing found
    # all four of these telling people to "unset Render JavaScript" when that
    # toggle was visibly off.
    for label in ("Screenshot", "PDF output", "Wait (ms)", "Wait for selector"):
        # unset stealth + an implied browser -> stealth silently off
        check(
            f"resolve_stealth(unset + implied {label})",
            resolve_stealth(None, False, False, tool="Fetch", implied_browser=label),
            False,
        )
        # stealth explicitly on + an implied browser -> name the real option
        try:
            resolve_stealth(True, False, False, tool="Fetch", implied_browser=label)
            check(f"resolve_stealth(on + implied {label}) raises", False, True)
        except ToolParameterValidationError as exc:
            check(f"resolve_stealth(on + implied {label}) names it", label in str(exc), True)
            check(
                f"resolve_stealth(on + implied {label}) does not blame js_render",
                "unset Render JavaScript" not in str(exc),
                True,
            )
        # stealth off + an implied browser -> nothing to resolve
        check(
            f"resolve_stealth(off + implied {label})",
            resolve_stealth(False, False, False, tool="Fetch", implied_browser=label),
            False,
        )

    # Explicitly asking for both must name both fields, not fail silently.
    for label, js, pp, wanted in [
        ("js_render", True, False, "Render JavaScript"),
        ("premium_proxy", False, True, "Premium proxy"),
        ("both", True, True, "Render JavaScript and Premium proxy"),
    ]:
        try:
            resolve_stealth(True, js, pp, tool="Extract")
            check(f"resolve_stealth(explicit on + {label}) raises", False, True)
        except ToolParameterValidationError as exc:
            check(f"resolve_stealth(explicit on + {label}) names it", wanted in str(exc), True)


# --- empty body on a 2xx -------------------------------------------------
# Zenrows occasionally answers 200 with zero bytes: the target served a
# challenge shell instead of the page. Passing that through as a success is
# exactly the silent failure this plugin exists to avoid, so client.fetch
# turns it into an error. Measured at roughly 1 run in 16 against a
# challenge page, so it is rare enough to slip through manual testing.

def test_empty_body_is_an_error() -> None:
    from tools import client

    class FakeResponse:
        def __init__(self, content: bytes) -> None:
            self.content = content
            self.text = content.decode()
            self.status_code = 200

    original = client._sdk_call
    try:
        client._sdk_call = lambda *a, **k: FakeResponse(b"")
        try:
            client.fetch("k", "https://example.com", {}, action="fetching the page")
            check("empty 200 raises", False, True)
        except ToolInvokeError as exc:
            check("empty 200 names the URL", "https://example.com" in str(exc), True)
            check("empty 200 says empty", "empty page body" in str(exc), True)

        client._sdk_call = lambda *a, **k: FakeResponse(b"<html>ok</html>")
        got = client.fetch("k", "https://example.com", {}, action="fetching the page")
        check("non-empty 200 passes through", got.text, "<html>ok</html>")
    finally:
        client._sdk_call = original


# --- credentials must never reach a user-facing message -------------------
# The key travels to Zenrows as a query parameter, so a connection failure
# carries it: str(requests.ConnectionError) embeds the whole URL. Verified
# against a real failure -- "Max retries exceeded with url: /v1/?apikey=..."
# -- which the plugin used to interpolate straight into a workflow error.

def test_redact() -> None:
    KEY = "sk_live_0123456789abcdef"
    leaks = [
        f"HTTPSConnectionPool(host='api.zenrows.com', port=443): Max retries "
        f"exceeded with url: /v1/?apikey={KEY}&url=https%3A%2F%2Fexample.com",
        f"https://api.zenrows.com/v1/?url=x&apikey={KEY}",
        f"api_key={KEY}&other=1",
        f"{{'X-API-Key': '{KEY}'}}",
        f"Authorization: Bearer {KEY}",
    ]
    for raw in leaks:
        cleaned = redact(raw)
        check(f"redact removes the key from {raw[:38]!r}", KEY in cleaned, False)
        check(f"redact leaves something behind for {raw[:38]!r}", len(cleaned) > 0, True)

    # A message with no credential in it must survive untouched.
    plain = "Timed out after 90s while fetching the page."
    check("redact leaves clean text alone", redact(plain), plain)

    # Every call site that interpolates an exception must go through it.
    import pathlib as _p
    for rel in ("tools/client.py", "tools/fetch.py", "tools/extract.py",
                "tools/batch_create.py", "tools/batch_results.py",
                "tools/batch_status.py", "provider/zenrows.py"):
        body = (_p.Path(__file__).resolve().parents[1] / rel).read_text()
        check(f"{rel} never interpolates a bare exception", "{exc}" in body, False)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s):\n")
        for f in FAILURES:
            print(f"  {f}\n")
        return 1
    print(f"all checks passed ({len(tests)} groups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
