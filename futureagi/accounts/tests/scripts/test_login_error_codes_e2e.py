#!/usr/bin/env python3
"""
Manual E2E test script for login structured error codes.

Run this against a live dev server to verify the full flow end-to-end.

Usage:
    python test_login_error_codes_e2e.py
    python test_login_error_codes_e2e.py --base-url http://localhost:8000
    python test_login_error_codes_e2e.py --base-url https://staging.futureagi.com

The script does NOT require pytest — just a running Django server and the
`requests` library. It exits with code 0 if all checks pass, 1 otherwise.

Pre-conditions:
  - A valid active user must exist. Set TEST_EMAIL / TEST_PASSWORD env vars,
    or accept the defaults (uses @futureagi.com to bypass reCAPTCHA on dev).
  - Redis must be reachable by the server (for rate-limit/block checks).

What is tested:
  1. Successful login           → HTTP 200, tokens present, no error_code
  2. Wrong password             → HTTP 400, LOGIN_INVALID_CREDENTIALS
  3. Non-existent email         → HTTP 400, LOGIN_INVALID_CREDENTIALS
  4. Deactivated user           → HTTP 400, LOGIN_ACCOUNT_DEACTIVATED  (manual setup required)
  5. Too-many-attempts (block)  → HTTP 400, LOGIN_TOO_MANY_ATTEMPTS    (hammers the endpoint)
  6. IP blocked                 → HTTP 403, LOGIN_IP_BLOCKED            (hammers /token/)
  7. PW reset rate limited      → HTTP 403, LOGIN_PASSWORD_RESET_RATE_LIMITED (hammers /password-reset-initiate/)
  8. Response envelope shape    → {status: false, result: {error, error_code}}
  9. No error_code in success   → Verify clean success response
"""

import argparse
import os
import sys
import time

try:
    import requests
except ImportError:
    print("❌  Please install the 'requests' library: pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_BASE = "http://localhost:8000"
TEST_EMAIL = os.environ.get("TEST_EMAIL", "testuser@futureagi.com")
TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "testpassword123")
WRONG_PASSWORD = "absolutelywrongpassword99"

PASS = "✅"
FAIL = "❌"
SKIP = "⏭️ "

results: list[tuple[str, bool, str]] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def check(name: str, condition: bool, detail: str = "") -> None:
    icon = PASS if condition else FAIL
    print(f"  {icon}  {name}", f"({detail})" if detail else "")
    results.append((name, condition, detail))


def post(base: str, path: str, payload: dict, **kwargs) -> requests.Response:
    url = base.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    return requests.post(url, json=payload, headers=headers, timeout=10, **kwargs)


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ---------------------------------------------------------------------------
# Test suites
# ---------------------------------------------------------------------------


def test_successful_login(base: str) -> None:
    section("1 · Successful login")
    resp = post(
        base, "/accounts/token/", {"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    check("HTTP 200", resp.status_code == 200, f"got {resp.status_code}")
    data = resp.json()
    check("access token present", "access" in data)
    check("refresh token present", "refresh" in data)
    check("no error_code in success", "error_code" not in data)
    check("no error_code in result", "error_code" not in data.get("result", {}))


def test_wrong_password(base: str) -> None:
    section("2 · Wrong password → LOGIN_INVALID_CREDENTIALS")
    resp = post(
        base, "/accounts/token/", {"email": TEST_EMAIL, "password": WRONG_PASSWORD}
    )
    check("HTTP 400", resp.status_code == 400, f"got {resp.status_code}")
    data = resp.json()
    result = data.get("result", data)
    check(
        "error_code = LOGIN_INVALID_CREDENTIALS",
        result.get("error_code") == "LOGIN_INVALID_CREDENTIALS",
        result.get("error_code"),
    )
    check(
        "error string unchanged",
        result.get("error") == "Invalid credentials",
        result.get("error"),
    )
    check("remaining_attempts present", "remaining_attempts" in result)
    check("status = false", data.get("status") is False)


def test_nonexistent_email(base: str) -> None:
    section("3 · Non-existent email → LOGIN_INVALID_CREDENTIALS")
    resp = post(
        base,
        "/accounts/token/",
        {"email": "nobody_xyz@futureagi.com", "password": "anypass"},
    )
    check("HTTP 400", resp.status_code == 400, f"got {resp.status_code}")
    result = resp.json().get("result", resp.json())
    check(
        "error_code = LOGIN_INVALID_CREDENTIALS",
        result.get("error_code") == "LOGIN_INVALID_CREDENTIALS",
        result.get("error_code"),
    )
    check(
        "error string unchanged",
        result.get("error") == "Invalid credentials",
        result.get("error"),
    )


def test_deactivated_user(base: str) -> None:
    section("4 · Deactivated user → LOGIN_ACCOUNT_DEACTIVATED  [manual setup required]")
    deactivated_email = os.environ.get("DEACTIVATED_EMAIL", "")
    if not deactivated_email:
        print(f"  {SKIP}  Set DEACTIVATED_EMAIL env var to test this path")
        return
    resp = post(
        base,
        "/accounts/token/",
        {"email": deactivated_email, "password": TEST_PASSWORD},
    )
    check("HTTP 400", resp.status_code == 400, f"got {resp.status_code}")
    result = resp.json().get("result", resp.json())
    check(
        "error_code = LOGIN_ACCOUNT_DEACTIVATED",
        result.get("error_code") == "LOGIN_ACCOUNT_DEACTIVATED",
        result.get("error_code"),
    )
    check(
        "error string unchanged",
        result.get("error") == "Account deactivated",
        result.get("error"),
    )
    check(
        "message includes 'deactivated'",
        "deactivated" in result.get("message", "").lower(),
    )
    check("no remaining_attempts", "remaining_attempts" not in result)


def test_account_blocked(base: str) -> None:
    """
    Hammer the endpoint enough times to exhaust login attempts (default 10),
    triggering LOGIN_TOO_MANY_ATTEMPTS, and then confirm the subsequent attempt
    returns LOGIN_ACCOUNT_BLOCKED.
    """
    section("5 · Account blocked → LOGIN_TOO_MANY_ATTEMPTS + LOGIN_ACCOUNT_BLOCKED")
    print(
        f"  ⚠️   This test sends many requests to {TEST_EMAIL}. It will lock that account for ~1 hour."
    )
    proceed = os.environ.get("TEST_ACCOUNT_BLOCK", "").lower() in ("1", "true", "yes")
    if not proceed:
        print(f"  {SKIP}  Set TEST_ACCOUNT_BLOCK=1 to run this test")
        return

    block_email = os.environ.get("BLOCK_TEST_EMAIL", TEST_EMAIL)
    last_resp = None
    triggered = False
    for i in range(12):
        resp = post(
            base, "/accounts/token/", {"email": block_email, "password": WRONG_PASSWORD}
        )
        data = resp.json()
        result = data.get("result", data)
        code = result.get("error_code", "")
        if code == "LOGIN_TOO_MANY_ATTEMPTS":
            triggered = True
            check(f"LOGIN_TOO_MANY_ATTEMPTS triggered on attempt {i+1}", True)
            check("blocked flag present", result.get("blocked") is True)
            check("block_time present", "block_time" in result)
            last_resp = resp
            break
        time.sleep(0.1)

    check("LOGIN_TOO_MANY_ATTEMPTS was triggered", triggered)

    if triggered:
        # One more attempt should see LOGIN_ACCOUNT_BLOCKED
        resp2 = post(
            base, "/accounts/token/", {"email": block_email, "password": WRONG_PASSWORD}
        )
        result2 = resp2.json().get("result", resp2.json())
        check(
            "Subsequent attempt → LOGIN_ACCOUNT_BLOCKED",
            result2.get("error_code") == "LOGIN_ACCOUNT_BLOCKED",
            result2.get("error_code"),
        )
        check("block_time_remaining present", "block_time_remaining" in result2)


def test_ip_blocked(base: str) -> None:
    """
    Hammer /accounts/token/ from the same IP to trigger the rate limit,
    then confirm the middleware returns JSON 403 with LOGIN_IP_BLOCKED.

    Default threshold: MAX_LOGIN_ATTEMPTS_PER_HOUR (typically 10).
    """
    section("6 · IP rate limited → LOGIN_IP_RATE_LIMITED / LOGIN_IP_BLOCKED")
    print(
        f"  ⚠️   This test sends many requests and may lock your IP. Uses a throwaway email."
    )
    proceed = os.environ.get("TEST_IP_BLOCK", "").lower() in ("1", "true", "yes")
    if not proceed:
        print(f"  {SKIP}  Set TEST_IP_BLOCK=1 to run this test")
        return

    ip_test_email = "iptest_throwaway@futureagi.com"
    triggered_rate = False
    triggered_block = False
    for i in range(15):
        resp = post(
            base,
            "/accounts/token/",
            {"email": ip_test_email, "password": WRONG_PASSWORD},
        )
        if resp.status_code == 403:
            data = resp.json()
            # Must be valid JSON with structured envelope
            check("403 response is valid JSON", isinstance(data, dict))
            check("status=false in envelope", data.get("status") is False)
            check("result key present", "result" in data)
            ec = data.get("result", {}).get("error_code", "")
            if ec == "LOGIN_IP_RATE_LIMITED":
                triggered_rate = True
                check("LOGIN_IP_RATE_LIMITED received", True)
                check("blocked flag in result", data["result"].get("blocked") is True)
            elif ec == "LOGIN_IP_BLOCKED":
                triggered_block = True
                check("LOGIN_IP_BLOCKED received", True)
            break
        time.sleep(0.05)

    check(
        "IP block/rate-limit was triggered (403 received)",
        triggered_rate or triggered_block,
    )


def test_password_reset_rate_limited(base: str) -> None:
    section("7 · Password reset rate limited → LOGIN_PASSWORD_RESET_RATE_LIMITED")
    proceed = os.environ.get("TEST_PW_RESET_RATE", "").lower() in ("1", "true", "yes")
    if not proceed:
        print(f"  {SKIP}  Set TEST_PW_RESET_RATE=1 to run this test")
        return

    triggered = False
    for i in range(15):
        resp = post(
            base,
            "/accounts/password-reset-initiate/",
            {"email": "pwreset_test@futureagi.com"},
        )
        if resp.status_code == 403:
            triggered = True
            data = resp.json()
            check("403 response is valid JSON", isinstance(data, dict))
            check("status=false", data.get("status") is False)
            check(
                "error_code = LOGIN_PASSWORD_RESET_RATE_LIMITED",
                data.get("result", {}).get("error_code")
                == "LOGIN_PASSWORD_RESET_RATE_LIMITED",
                data.get("result", {}).get("error_code"),
            )
            break
        time.sleep(0.05)

    check("Rate limit was triggered", triggered)


def test_response_envelope(base: str) -> None:
    section("8 · Response envelope shape on all error paths")
    resp = post(
        base, "/accounts/token/", {"email": TEST_EMAIL, "password": WRONG_PASSWORD}
    )
    data = resp.json()
    check("top-level 'status' key present", "status" in data)
    check("top-level 'result' key present", "result" in data)
    check("result.error present", "error" in data.get("result", {}))
    check("result.error_code present", "error_code" in data.get("result", {}))
    check(
        "result.error_code is a string",
        isinstance(data.get("result", {}).get("error_code"), str),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Login error codes E2E test")
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE, help="Base URL of the Django server"
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print(f"\n🔍  Login Error Codes — E2E Test Suite")
    print(f"    Server : {base}")
    print(f"    Email  : {TEST_EMAIL}")

    test_successful_login(base)
    test_wrong_password(base)
    test_nonexistent_email(base)
    test_deactivated_user(base)
    test_account_blocked(base)
    test_ip_blocked(base)
    test_password_reset_rate_limited(base)
    test_response_envelope(base)

    # Summary
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed

    print(f"\n{'═' * 60}")
    print(f"  Results: {passed}/{total} passed  {'🎉' if failed == 0 else '💥'}")
    if failed:
        print(f"\n  Failed checks:")
        for name, ok, detail in results:
            if not ok:
                print(f"    {FAIL}  {name}", f"→ {detail}" if detail else "")
    print(f"{'═' * 60}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
