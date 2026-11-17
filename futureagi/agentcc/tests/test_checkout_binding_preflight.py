"""POST /agentcc/managed-credits/checkout/ binds the session it opens (MUST-FIX G).

When cloud billing returns a Checkout session, FA records which org opened it
(a sha256 digest of the id, never the id), for which amount and in which
mode, before the checkout URL is answered: nobody can pay a session the
landing page could not bind. A live-mode deployment opens no live session
unless the owner-go setting BILLING_LEDGER_ACCEPT_LIVE accepts live money.

Cloud billing is the wallet suite's stand-in module; Stripe is
never called. Session ids are synthetic and generated at runtime.
"""

import hashlib
import secrets
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from conftest import WorkspaceAwareAPIClient
from django.db import DatabaseError

from agentcc.models import AgentccCheckoutBinding
from agentcc.services import checkout_verification
from agentcc.tests import test_managed_credits_api as wallet
from agentcc.tests.test_managed_credits_api import (
    BASE,
    CHECKOUT_BODY,
    _install_checkout,
    _user_in,
)
from tfc.constants.levels import Level

pytestmark = pytest.mark.integration

other_org = wallet.other_org
MICROS = 1_000_000


def _require(condition, message):
    # A fixed message only: never a session id.
    if not condition:
        pytest.fail(message, pytrace=False)


def _session(mode="test"):
    return f"cs_{mode}_" + secrets.token_hex(16)


def _opened(session_id):
    return {
        "checkout_url": "https://checkout.test/s/" + session_id,
        "session_id": session_id,
    }


@pytest.fixture
def admin(organization):
    """An org Admin and an authenticated client acting in ``organization``."""
    user, workspace = _user_in(organization, Level.ADMIN)
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    yield user, client
    client.stop_workspace_injection()


def _post(client):
    return client.post(f"{BASE}checkout/", CHECKOUT_BODY, format="json")


# --- T2.1 ---------------------------------------------------------------------


def test_post_records_binding_without_raw_session_id(admin, organization):
    user, client = admin
    session = _session()
    create_session = MagicMock(return_value=_opened(session))
    with _install_checkout(create_session):
        response = _post(client)

    assert response.status_code == 200, response.status_code
    _require(
        response.json()["result"] == _opened(session),
        "the checkout response changed shape",
    )
    bindings = list(AgentccCheckoutBinding.objects.filter(organization=organization))
    assert len(bindings) == 1
    binding = bindings[0]
    _require(
        binding.session_digest == hashlib.sha256(session.encode()).hexdigest(),
        "the binding is not keyed by the sha256 of the session id",
    )
    assert binding.amount_micros == 25 * MICROS
    assert binding.currency == "usd"
    assert binding.livemode is False
    assert binding.created_by_id == user.pk
    stored = [
        str(getattr(binding, field.attname))
        for field in AgentccCheckoutBinding._meta.concrete_fields
    ]
    _require(
        not any(session in value for value in stored),
        "a binding column holds the raw session id",
    )


# --- T2.2 ---------------------------------------------------------------------


def test_live_checkout_refused_unless_owner_go(admin, organization, settings):
    settings.STRIPE_LIVE = True
    settings.BILLING_LEDGER_ACCEPT_LIVE = False
    _, client = admin
    session = _session("live")
    create_session = MagicMock(return_value=_opened(session))
    with _install_checkout(create_session):
        response = _post(client)

    assert response.status_code == 503, response.status_code
    assert response.json()["code"] == "billing_unavailable"
    body = response.content.decode()
    assert "checkout_url" not in body
    _require(session not in body, "the refusal names the session")
    create_session.assert_not_called()
    assert not AgentccCheckoutBinding.objects.filter(organization=organization).exists()


# --- T2.3 ---------------------------------------------------------------------


def test_live_checkout_allowed_with_owner_go_binds_livemode(
    admin, organization, settings
):
    settings.STRIPE_LIVE = True
    settings.BILLING_LEDGER_ACCEPT_LIVE = True
    _, client = admin
    session = _session("live")
    create_session = MagicMock(return_value=_opened(session))
    with _install_checkout(create_session):
        response = _post(client)

    assert response.status_code == 200, response.status_code
    create_session.assert_called_once()
    binding = AgentccCheckoutBinding.objects.get(organization=organization)
    assert binding.livemode is True
    _require(
        binding.session_digest == hashlib.sha256(session.encode()).hexdigest(),
        "the live binding is not keyed by the session digest",
    )


# --- T2.4 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [DatabaseError, checkout_verification.CheckoutBindingConflict],
    ids=["database-error", "binding-conflict"],
)
def test_binding_failure_returns_no_checkout_url(admin, organization, failure):
    _, client = admin
    session = _session()
    create_session = MagicMock(return_value=_opened(session))
    with (
        _install_checkout(create_session),
        patch.object(
            checkout_verification, "record_binding", side_effect=failure(session)
        ) as record,
    ):
        response = _post(client)

    assert response.status_code == 503, response.status_code
    assert response.json()["code"] == "billing_unavailable"
    body = response.content.decode()
    assert "checkout_url" not in body
    _require(session not in body, "the refusal names the session")
    assert record.call_count == 1
    org, session_id, amount, livemode, actor = record.call_args.args
    assert org == organization
    _require(session_id == session, "the binding was not for the opened session")
    assert amount == Decimal("25.00")
    assert livemode is False
    assert actor.is_authenticated


# --- T2.5 ---------------------------------------------------------------------


def test_same_session_rebind_is_idempotent_other_org_conflicts(organization, other_org):
    session = _session()
    first = checkout_verification.record_binding(
        organization, session, Decimal("25.00"), False, None
    )
    again = checkout_verification.record_binding(
        organization, session, Decimal("25.00"), False, None
    )
    assert first.pk == again.pk
    assert AgentccCheckoutBinding.objects.count() == 1

    with pytest.raises(checkout_verification.CheckoutBindingConflict):
        checkout_verification.record_binding(
            other_org, session, Decimal("25.00"), False, None
        )
    with pytest.raises(checkout_verification.CheckoutBindingConflict):
        checkout_verification.record_binding(
            organization, session, Decimal("30.00"), False, None
        )
    with pytest.raises(checkout_verification.CheckoutBindingConflict):
        checkout_verification.record_binding(
            organization, session, Decimal("25.00"), True, None
        )
    binding = AgentccCheckoutBinding.objects.get()
    assert binding.organization_id == organization.pk
    assert binding.amount_micros == 25 * MICROS
    assert binding.livemode is False
    assert binding.created_by is None


def test_binding_conflict_is_value_free(organization, other_org):
    session = _session()
    checkout_verification.record_binding(
        organization, session, Decimal("25.00"), False, None
    )
    with pytest.raises(checkout_verification.CheckoutBindingConflict) as raised:
        checkout_verification.record_binding(
            other_org, session, Decimal("25.00"), False, None
        )
    text = f"{raised.value} {raised.value!r} {raised.value.args}"
    _require(session not in text, "the conflict names the session")
    _require(
        hashlib.sha256(session.encode()).hexdigest() not in text,
        "the conflict names the session digest",
    )
