"""Stripe billing settings resolved from the environment (MUST-FIX G, FA sites).

``resolve_stripe_webhook_secret`` never yields an empty signing secret: a
deployment with a Stripe key and no secret for its mode refuses to start, and
one without a key gets a per-process refusal value that verifies no event,
including one forged with the empty key. ``resolve_accept_live`` turns live
money on only for the exact value ``true`` with an owner-go record reference.

Every value here is synthetic and generated at runtime. Failures report fixed
messages, never a value: assertion rewriting must not print a secret.
"""

import hashlib
import hmac
import secrets
import time

import pytest
import stripe
from django.core.exceptions import ImproperlyConfigured

from tfc.settings.stripe_billing import (
    resolve_accept_live,
    resolve_stripe_webhook_secret,
)

pytestmark = pytest.mark.unit

LIVE = "WEBHOOK_SECRET_LIVE"
TEST = "WEBHOOK_SECRET_TEST"
ACCEPT_LIVE = "BILLING_LEDGER_ACCEPT_LIVE"
OWNER_GO = "BILLING_LEDGER_ACCEPT_LIVE_OWNER_GO"


def _require(condition, message):
    # A fixed message only: never a compared value.
    if not condition:
        pytest.fail(message, pytrace=False)


def _synthetic_secret():
    return "whsec_SYNTHETIC_NOT_A_SECRET_" + secrets.token_hex(8)


def _signed_header(payload, key, timestamp=None):
    """A Stripe-Signature header for ``payload`` under ``key`` (the v1 scheme:
    HMAC-SHA256 over ``<t>.<payload>``), built with the standard library."""
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(
        key.encode("utf-8"), f"{timestamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _event_payload():
    event_id = "evt_" + secrets.token_hex(12)
    return f'{{"id": "{event_id}", "type": "checkout.session.completed"}}'


def _verifies(payload, header, secret):
    try:
        stripe.WebhookSignature.verify_header(payload, header, secret, tolerance=300)
    except stripe.SignatureVerificationError:
        return False
    return True


# --- T1.1 ---------------------------------------------------------------------


@pytest.mark.parametrize("stripe_live", [False, True], ids=["test-mode", "live-mode"])
@pytest.mark.parametrize(
    "env",
    [{}, {TEST: "", LIVE: ""}, {TEST: "   ", LIVE: "\t\n"}],
    ids=["unset", "empty", "whitespace"],
)
def test_unset_secret_without_stripe_resolves_to_unguessable_refusal(env, stripe_live):
    first, configured = resolve_stripe_webhook_secret(
        env, stripe_live=stripe_live, stripe_enabled=False
    )
    second, configured_again = resolve_stripe_webhook_secret(
        env, stripe_live=stripe_live, stripe_enabled=False
    )
    _require(isinstance(first, str) and first.strip() != "", "value is empty")
    _require(len(first) >= 64, "refusal value is too short to be unguessable")
    _require(configured is False and configured_again is False, "configured")
    _require(first != second, "refusal value repeats between resolutions")
    _require(
        first not in env.values() and second not in env.values(),
        "refusal value came from the environment",
    )


# --- T1.2 ---------------------------------------------------------------------


@pytest.mark.parametrize("env", [{}, {TEST: ""}, {TEST: "  "}])
def test_empty_key_forged_event_is_refused_by_resolved_secret(env):
    secret, configured = resolve_stripe_webhook_secret(
        env, stripe_live=False, stripe_enabled=False
    )
    payload = _event_payload()
    forged = _signed_header(payload, "")
    _require(configured is False, "an unset secret reads as configured")
    _require(
        _verifies(payload, forged, secret) is False,
        "an event signed with the empty key verified against the resolved value",
    )
    # The same check with the standard library: a receiver that compares the
    # HMAC itself refuses the forgery too.
    timestamp = forged.split(",")[0][2:]
    expected = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    _require(
        hmac.compare_digest(f"t={timestamp},v1={expected}", forged) is False,
        "the resolved value signs like the empty key",
    )


def test_configured_secret_verifies_its_own_events_positive_control():
    real = _synthetic_secret()
    secret, configured = resolve_stripe_webhook_secret(
        {TEST: real}, stripe_live=False, stripe_enabled=True
    )
    payload = _event_payload()
    _require(configured is True, "a set secret reads as unconfigured")
    _require(
        _verifies(payload, _signed_header(payload, real), secret) is True,
        "an event signed with the configured secret did not verify",
    )
    _require(
        _verifies(payload, _signed_header(payload, ""), secret) is False,
        "an event signed with the empty key verified",
    )


# --- T1.3 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "stripe_live, missing, other",
    [(False, TEST, LIVE), (True, LIVE, TEST)],
    ids=["test-key", "live-key"],
)
@pytest.mark.parametrize("blank", [None, "", "   "], ids=["unset", "empty", "spaces"])
def test_stripe_enabled_without_secret_refuses_to_start(
    stripe_live, missing, other, blank
):
    # The other mode's secret is set: it is never used, and never echoed.
    other_value = _synthetic_secret()
    env = {other: other_value}
    if blank is not None:
        env[missing] = blank
    with pytest.raises(ImproperlyConfigured) as raised:
        resolve_stripe_webhook_secret(env, stripe_live=stripe_live, stripe_enabled=True)
    message = str(raised.value)
    _require(missing in message, "the refusal does not name the missing variable")
    _require(other_value not in message, "the refusal echoes a supplied value")
    _require(raised.value.__cause__ is None, "the refusal chains another error")
    _require(raised.value.__context__ is None, "the refusal carries a context")


# --- T1.4 ---------------------------------------------------------------------


@pytest.mark.parametrize("stripe_enabled", [True, False])
def test_configured_secret_selected_by_key_mode(stripe_enabled):
    live_value, test_value = _synthetic_secret(), _synthetic_secret()
    env = {LIVE: f"  {live_value}\n", TEST: f"\t{test_value} "}

    live, live_configured = resolve_stripe_webhook_secret(
        env, stripe_live=True, stripe_enabled=stripe_enabled
    )
    test, test_configured = resolve_stripe_webhook_secret(
        env, stripe_live=False, stripe_enabled=stripe_enabled
    )

    _require(live == live_value, "live mode did not pick the stripped live secret")
    _require(test == test_value, "test mode did not pick the stripped test secret")
    _require(live_configured is True and test_configured is True, "configured")

    only_test, _ = resolve_stripe_webhook_secret(
        {TEST: test_value}, stripe_live=True, stripe_enabled=False
    )
    _require(only_test != test_value, "live mode fell back to the test secret")


# --- T1.5 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "env",
    [
        {},
        {ACCEPT_LIVE: ""},
        {ACCEPT_LIVE: "   "},
        {ACCEPT_LIVE: "false"},
        {ACCEPT_LIVE: "FALSE"},
        {ACCEPT_LIVE: " False "},
        {ACCEPT_LIVE: "false", OWNER_GO: "owner-go/2026-11-11"},
    ],
    ids=[
        "unset",
        "empty",
        "spaces",
        "false",
        "upper",
        "padded",
        "false-with-ref",
    ],
)
def test_accept_live_defaults_to_refusing_live_money(env):
    accepted, reference = resolve_accept_live(env)
    _require(accepted is False, "live money accepted without the owner-go setting")
    _require(reference == "", "an owner-go reference without acceptance")


@pytest.mark.parametrize("value", ["true", "TRUE", " True "])
def test_accept_live_true_needs_an_owner_go_record(value):
    reference = "owner-go/RL-" + secrets.token_hex(4)
    accepted, recorded = resolve_accept_live({ACCEPT_LIVE: value, OWNER_GO: reference})
    _require(accepted is True, "the exact value true did not accept live money")
    _require(recorded == reference, "the owner-go reference was not kept")

    padded, recorded = resolve_accept_live(
        {ACCEPT_LIVE: value, OWNER_GO: f"  {reference}\n"}
    )
    _require(padded is True and recorded == reference, "reference not stripped")


@pytest.mark.parametrize(
    "owner_go",
    [None, "", "   ", "ab", "x" * 129, "owner go", "owner-go;rm", "go\nnext"],
    ids=[
        "unset",
        "empty",
        "spaces",
        "too-short",
        "too-long",
        "space-inside",
        "semicolon",
        "newline",
    ],
)
def test_accept_live_true_without_a_valid_owner_go_refuses_to_start(owner_go):
    env = {ACCEPT_LIVE: "true"}
    if owner_go is not None:
        env[OWNER_GO] = owner_go
    with pytest.raises(ImproperlyConfigured) as raised:
        resolve_accept_live(env)
    message = str(raised.value)
    _require(OWNER_GO in message, "the refusal does not name the owner-go variable")
    if owner_go and len(owner_go.strip()) >= 6:
        _require(owner_go.strip() not in message, "the refusal echoes the value")


@pytest.mark.parametrize(
    "value", ["1", "yes", "on", "tru", "t", "y", "0", "no", "off", "enabled"]
)
def test_accept_live_is_strict_and_owner_go_bound(value):
    """Only true or false: any other spelling refuses to start, never read as
    either one."""
    marker = value + "-" + secrets.token_hex(4)
    for raw in (value, marker):
        env = {ACCEPT_LIVE: raw, OWNER_GO: "owner-go/" + secrets.token_hex(4)}
        with pytest.raises(ImproperlyConfigured) as raised:
            resolve_accept_live(env)
        message = str(raised.value)
        _require(ACCEPT_LIVE in message, "the refusal does not name the variable")
        _require(marker not in message, "the refusal echoes the value")
        _require(env[OWNER_GO] not in message, "the refusal echoes the reference")
