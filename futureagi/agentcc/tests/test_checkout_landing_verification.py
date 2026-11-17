"""The landing page fulfils only a bound, paid session (MUST-FIX G).

GET /agentcc/managed-credits/checkout/<id>/ used to run cloud billing's fulfil
for any well-formed session id. FA now checks first, in a fixed order: the
id's mode prefix, live acceptance, the org binding POST checkout/ recorded,
the binding's mode, then Stripe's own Checkout Session: the same id, a
payment, complete, paid, in this mode, in USD, for at least the bound amount.
Any refusal or error leaves the session pending and is logged once with the
org and a fixed reason, never the session id.

The ledger boundary and cloud billing are stand-ins, and the Stripe read is
mocked at its one function (read_checkout_session); the real SDK path is
test_checkout_verification_sdk. Mocks never certify money: the lead's
test-mode run through Stripe does. Session ids are generated at runtime.
"""

import hashlib
import logging
import secrets
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db import DatabaseError

from agentcc.models import AgentccCheckoutBinding
from agentcc.services import checkout_verification
from agentcc.services.checkout_verification import (
    CheckoutReadRefused,
    CheckoutSessionState,
)
from agentcc.tests import test_managed_credits_api as wallet
from agentcc.tests.test_managed_credits_api import BASE, _install_checkout
from agentcc.views import managed_credits as views

pytestmark = pytest.mark.integration

admin_client = wallet.admin_client
make_client = wallet.make_client
other_org = wallet.other_org

MICROS = 1_000_000
NOT_FULFILLED = "managed_credits_landing_not_fulfilled"
FULFIL_REFUSED = "managed_credits_landing_fulfil_refused"
PENDING = {"session_id": None, "status": "pending", "credited": False}
# Stands for another runtime-generated session id in a parametrized case.
OTHER_SESSION = object()


def _require(condition, message):
    # A fixed message only: never a session id.
    if not condition:
        pytest.fail(message, pytrace=False)


def _session(mode="test"):
    return f"cs_{mode}_" + secrets.token_hex(16)


def _bind(org, session_id, *, amount_micros=25 * MICROS, livemode=False):
    """The binding POST checkout/ records, written directly."""
    return AgentccCheckoutBinding.objects.create(
        organization=org,
        session_digest=hashlib.sha256(session_id.encode()).hexdigest(),
        amount_micros=amount_micros,
        currency="usd",
        livemode=livemode,
    )


def _paid(session_id, **fields):
    """Stripe's answer for a complete, paid, test-mode USD 25.00 session."""
    state = CheckoutSessionState(
        id=session_id,
        mode="payment",
        status="complete",
        payment_status="paid",
        livemode=False,
        currency="usd",
        amount_total=2500,
    )
    return replace(state, **fields)


@pytest.fixture
def landing():
    """The landing route with the ledger boundary and cloud billing stood in.

    ``credited`` is the ledger: fulfil credits a session once, and the route's
    credited read follows it. ``read`` is the one Stripe read."""
    credited = set()

    def is_credited(ledger, org, session_id):
        return (org.pk, session_id) in credited

    def fulfil(org, session_id):
        credited.add((org.pk, session_id))

    fulfilment = MagicMock(side_effect=fulfil)
    read = MagicMock(name="read_checkout_session")
    with ExitStack() as stack:
        stack.enter_context(_install_checkout(MagicMock(), fulfil=fulfilment))
        stack.enter_context(patch.object(views, "_ledger", MagicMock()))
        stack.enter_context(
            patch.object(views, "checkout_credited", MagicMock(side_effect=is_credited))
        )
        stack.enter_context(
            patch.object(checkout_verification, "read_checkout_session", read)
        )
        yield SimpleNamespace(fulfil=fulfilment, read=read, credited=credited)


@pytest.fixture
def diagnostics(caplog):
    caplog.set_level(logging.WARNING)
    return caplog


def _get(client, session_id):
    response = client.get(f"{BASE}checkout/{session_id}/")
    assert response.status_code == 200, response.status_code
    return response


def _records(diagnostics, event):
    return [r for r in diagnostics.records if event in r.getMessage()]


def _value_free(record, session_id):
    surfaces = (
        record.getMessage(),
        str(vars(record)),
        logging.Formatter().format(record),
        str(record.exc_text),
    )
    _require(
        not any(session_id in text for text in surfaces),
        "the session id reached a log record",
    )
    assert record.exc_info is None
    assert record.exc_text is None
    assert isinstance(record.msg, dict)
    for key in ("session_id", "exception", "exc_info", "stripe_body", "body"):
        assert key not in record.msg, key


def _not_fulfilled(diagnostics, session_id, org, reason, exception_type=None):
    """Exactly one not-fulfilled record, with the org and a fixed reason."""
    records = _records(diagnostics, NOT_FULFILLED)
    assert len(records) == 1
    record = records[0]
    _value_free(record, session_id)
    assert record.msg["org_id"] == str(org.pk)
    assert record.msg["reason"] == reason
    if exception_type is None:
        assert "exception_type" not in record.msg
    else:
        assert record.msg["exception_type"] == exception_type
    # Fulfilment's own refusal event stays exclusive to fulfil failures.
    assert _records(diagnostics, FULFIL_REFUSED) == []


def _pending(response, session_id):
    _require(session_id not in response.content.decode(), "the id was echoed")
    assert response.json()["result"] == PENDING


# --- T3.1 ---------------------------------------------------------------------


def test_unbound_session_never_reaches_stripe_or_fulfil(
    admin_client, organization, landing, diagnostics
):
    session = _session()
    response = _get(admin_client, session)

    _pending(response, session)
    landing.read.assert_not_called()
    landing.fulfil.assert_not_called()
    _not_fulfilled(diagnostics, session, organization, "unbound")


# --- T3.2 ---------------------------------------------------------------------


def test_other_orgs_session_never_reaches_stripe_or_fulfil(
    admin_client, organization, other_org, landing, diagnostics
):
    session = _session()
    _bind(other_org, session)
    landing.read.return_value = _paid(session)

    response = _get(admin_client, session)

    _pending(response, session)
    landing.read.assert_not_called()
    landing.fulfil.assert_not_called()
    # Indistinguishable from an unknown session: the same reason.
    _not_fulfilled(diagnostics, session, organization, "unbound")


# --- T3.3 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields, reason",
    [
        ({"status": "open", "payment_status": "unpaid"}, "not_complete"),
        ({"status": "expired", "payment_status": "unpaid"}, "not_complete"),
        ({"status": "open"}, "not_complete"),
        ({"payment_status": "unpaid"}, "not_paid"),
        ({"payment_status": "no_payment_required"}, "not_paid"),
        ({"payment_status": None}, "not_paid"),
        ({"mode": "subscription"}, "not_payment"),
        ({"mode": "setup"}, "not_payment"),
        ({"status": None}, "not_complete"),
    ],
    ids=[
        "open-unpaid",
        "expired",
        "open",
        "unpaid",
        "no-payment-required",
        "payment-status-missing",
        "subscription",
        "setup",
        "status-missing",
    ],
)
def test_unpaid_states_never_fulfil(
    admin_client, organization, landing, diagnostics, fields, reason
):
    session = _session()
    _bind(organization, session)
    landing.read.return_value = _paid(session, **fields)

    response = _get(admin_client, session)

    _pending(response, session)
    landing.read.assert_called_once()
    _require(landing.read.call_args.args == (session,), "read another session")
    landing.fulfil.assert_not_called()
    _not_fulfilled(diagnostics, session, organization, reason)


# --- T3.4 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields, reason",
    [
        ({"livemode": True}, "mode_mismatch"),
        ({"livemode": None}, "mode_mismatch"),
        ({"currency": "eur"}, "currency_mismatch"),
        ({"currency": None}, "currency_mismatch"),
        ({"amount_total": 2499}, "amount_below_request"),
        ({"amount_total": 0}, "amount_below_request"),
        ({"amount_total": None}, "amount_below_request"),
        ({"id": OTHER_SESSION}, "session_mismatch"),
        ({"id": None}, "session_mismatch"),
    ],
    ids=[
        "livemode",
        "livemode-missing",
        "eur",
        "currency-missing",
        "one-cent-short",
        "zero",
        "amount-missing",
        "other-session",
        "id-missing",
    ],
)
def test_mode_amount_currency_mismatch_never_fulfil(
    admin_client, organization, landing, diagnostics, fields, reason
):
    session = _session()
    _bind(organization, session)
    fields = {
        name: _session() if value is OTHER_SESSION else value
        for name, value in fields.items()
    }
    landing.read.return_value = _paid(session, **fields)

    response = _get(admin_client, session)

    _pending(response, session)
    landing.read.assert_called_once()
    landing.fulfil.assert_not_called()
    _not_fulfilled(diagnostics, session, organization, reason)


def test_uppercase_usd_and_overpayment_still_fulfil(
    admin_client, organization, landing
):
    session = _session()
    _bind(organization, session)
    landing.read.return_value = _paid(session, currency="USD", amount_total=2600)

    response = _get(admin_client, session)

    assert response.json()["result"]["credited"] is True
    landing.fulfil.assert_called_once()


def test_binding_mode_must_match_the_deployment(
    admin_client, organization, landing, diagnostics
):
    session = _session()
    _bind(organization, session, livemode=True)
    landing.read.return_value = _paid(session)

    response = _get(admin_client, session)

    _pending(response, session)
    landing.read.assert_not_called()
    landing.fulfil.assert_not_called()
    _not_fulfilled(diagnostics, session, organization, "mode_mismatch")


# --- T3.5 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "refusal, exception_type",
    [
        (
            CheckoutReadRefused("stripe_read_failed", "APIConnectionError"),
            "APIConnectionError",
        ),
        (
            CheckoutReadRefused("stripe_read_failed", "InvalidRequestError"),
            "InvalidRequestError",
        ),
        (CheckoutReadRefused("stripe_not_configured"), None),
    ],
    ids=["connection", "not-found", "not-configured"],
)
def test_stripe_read_failure_is_pending_and_value_free(
    admin_client, organization, landing, diagnostics, refusal, exception_type
):
    session = _session()
    _bind(organization, session)
    landing.read.side_effect = refusal

    response = _get(admin_client, session)

    _pending(response, session)
    landing.read.assert_called_once()
    landing.fulfil.assert_not_called()
    _not_fulfilled(diagnostics, session, organization, refusal.code, exception_type)


# --- T3.6 ---------------------------------------------------------------------


def test_live_session_refused_without_owner_go_and_on_test_key(
    admin_client, organization, landing, diagnostics, settings
):
    session = _session("live")
    _bind(organization, session, livemode=True)
    landing.read.return_value = _paid(session, livemode=True)

    # A test-mode deployment never fulfils a live session.
    settings.STRIPE_LIVE = False
    settings.BILLING_LEDGER_ACCEPT_LIVE = True
    _pending(_get(admin_client, session), session)
    _not_fulfilled(diagnostics, session, organization, "mode_mismatch")

    # A live deployment without the owner-go setting refuses live money.
    diagnostics.clear()
    settings.STRIPE_LIVE = True
    settings.BILLING_LEDGER_ACCEPT_LIVE = False
    _pending(_get(admin_client, session), session)
    _not_fulfilled(diagnostics, session, organization, "live_not_accepted")

    landing.read.assert_not_called()
    landing.fulfil.assert_not_called()


def test_test_session_refused_on_a_live_deployment(
    admin_client, organization, landing, diagnostics, settings
):
    settings.STRIPE_LIVE = True
    settings.BILLING_LEDGER_ACCEPT_LIVE = True
    session = _session()
    _bind(organization, session)
    landing.read.return_value = _paid(session)

    _pending(_get(admin_client, session), session)

    landing.read.assert_not_called()
    landing.fulfil.assert_not_called()
    _not_fulfilled(diagnostics, session, organization, "mode_mismatch")


def test_live_session_with_owner_go_fulfils_positive_control(
    admin_client, organization, landing, settings
):
    settings.STRIPE_LIVE = True
    settings.BILLING_LEDGER_ACCEPT_LIVE = True
    session = _session("live")
    _bind(organization, session, livemode=True)
    landing.read.return_value = _paid(session, livemode=True)

    response = _get(admin_client, session)

    assert response.json()["result"]["credited"] is True
    landing.fulfil.assert_called_once()


# --- T3.7 ---------------------------------------------------------------------


def test_bound_paid_session_fulfils_once_positive_control(
    admin_client, organization, landing, diagnostics
):
    session = _session()
    _bind(organization, session)
    landing.read.return_value = _paid(session)

    first = _get(admin_client, session)
    again = _get(admin_client, session)

    _require(
        first.json()["result"]
        == {"session_id": session, "status": "complete", "credited": True},
        "a bound, paid session was not credited",
    )
    assert again.json()["result"]["credited"] is True
    landing.read.assert_called_once()
    landing.fulfil.assert_called_once()
    org, session_id = landing.fulfil.call_args.args
    assert org == organization
    _require(session_id == session, "fulfil ran for another session")
    assert landing.credited == {(organization.pk, session)}
    assert _records(diagnostics, NOT_FULFILLED) == []


# --- T3.8 ---------------------------------------------------------------------


def test_already_credited_skips_verification(admin_client, organization, landing):
    session = _session()
    landing.credited.add((organization.pk, session))

    response = _get(admin_client, session)

    assert response.json()["result"]["credited"] is True
    landing.read.assert_not_called()
    landing.fulfil.assert_not_called()


# --- T3.9 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "target, failure, exception_type",
    [
        ("bound_session", DatabaseError, "DatabaseError"),
        ("landing_refusal", RuntimeError, "RuntimeError"),
    ],
    ids=["binding-lookup", "verification"],
)
def test_verification_exception_is_contained_pending_and_value_free(
    admin_client,
    organization,
    landing,
    diagnostics,
    target,
    failure,
    exception_type,
):
    session = _session()
    _bind(organization, session)
    landing.read.return_value = _paid(session)

    with patch.object(
        checkout_verification, target, side_effect=failure(session)
    ) as failing:
        response = _get(admin_client, session)

    _pending(response, session)
    assert failing.call_count == 1
    landing.read.assert_not_called()
    landing.fulfil.assert_not_called()
    _not_fulfilled(
        diagnostics,
        session,
        organization,
        "verification_failed",
        exception_type,
    )


def test_landing_refusal_order_is_fixed(organization, other_org, settings):
    """landing_refusal directly: each check refuses before the next one runs."""
    session = _session()
    with patch.object(checkout_verification, "read_checkout_session") as read:
        refusal = checkout_verification.landing_refusal(organization, session)
        assert refusal.reason == "unbound"
        read.assert_not_called()

        _bind(organization, session)
        read.return_value = _paid(session)
        assert checkout_verification.landing_refusal(organization, session) is None
        assert checkout_verification.landing_refusal(other_org, session).reason == (
            "unbound"
        )
        assert read.call_count == 1

        settings.STRIPE_LIVE = True
        settings.BILLING_LEDGER_ACCEPT_LIVE = True
        assert checkout_verification.landing_refusal(organization, session).reason == (
            "mode_mismatch"
        )
        assert read.call_count == 1
