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
    as_bool,
    error_code,
    error_detail,
    is_domain_scoped_extract_restriction,
    is_extract_domain_not_enabled,
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
