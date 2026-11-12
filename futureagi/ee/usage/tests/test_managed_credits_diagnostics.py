"""Ledger-owned hooks keep supplied values out of exceptions and push logs."""

from unittest.mock import patch

import httpx
import pytest

from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests import test_managed_credits_diagnostics as wallet

pytestmark = pytest.mark.integration

ledger = wallet.ledger
other_org = wallet.other_org
gateway = wallet.gateway
supplied = wallet.supplied
diagnostics = wallet.diagnostics
_absent = wallet._absent
_records_without_values = wallet._records_without_values


def test_topup_conflict_exception_is_value_free(
    ledger, organization, other_org, supplied
):
    ledger.apply_topup(
        other_org,
        1_000_000,
        stripe_session_id=supplied,
        payment_intent_id="pi_diagnostics_conflict",
    )
    with pytest.raises(ledger.IdempotencyConflict) as caught:
        ledger.apply_topup(
            organization,
            1_000_000,
            stripe_session_id=supplied,
            payment_intent_id="pi_diagnostics_conflict",
        )
    _absent(supplied, str(caught.value), repr(caught.value))


def test_refund_refusal_exception_is_value_free(ledger, organization, supplied):
    with pytest.raises(ledger.RefundExceedsSpend) as caught:
        ledger.refund(organization, supplied, reason="test", actor=None)
    _absent(supplied, str(caught.value), repr(caught.value))


def test_topup_amount_exception_is_value_free(ledger, organization, supplied):
    with pytest.raises(ledger.ManagedCreditError) as caught:
        ledger.apply_topup(
            organization,
            supplied,
            stripe_session_id="cs_test",
            payment_intent_id="pi_diagnostics_amount",
        )
    _absent(supplied, str(caught.value), repr(caught.value))


@pytest.mark.parametrize("failure", [GatewayClientError, httpx.HTTPError, ValueError])
def test_topup_push_failure_diagnostics_are_value_free(
    ledger,
    organization,
    gateway,
    supplied,
    diagnostics,
    django_capture_on_commit_callbacks,
    failure,
):
    gateway.error = failure(supplied)
    with django_capture_on_commit_callbacks(execute=True):
        ledger.apply_topup(
            organization,
            1_000_000,
            stripe_session_id="cs_test",
            payment_intent_id="pi_diagnostics_push",
        )
    _records_without_values(
        diagnostics,
        supplied,
        "managed_credits_push_failed",
        failure.__name__,
        organization,
    )
    records = [
        r
        for r in diagnostics.records
        if "managed_credits_push_failed" in r.getMessage()
    ]
    assert records[0].msg["reason"] == "gateway_push_failed"


def test_topup_push_ack_diagnostics_are_value_free(
    ledger, organization, supplied, diagnostics, django_capture_on_commit_callbacks
):
    from ee.usage.services import gateway_push

    with (
        patch.object(
            gateway_push.gateway_credits,
            "put_granted",
            return_value={"granted_version": supplied},
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        ledger.apply_topup(
            organization,
            1_000_000,
            stripe_session_id="cs_test",
            payment_intent_id="pi_diagnostics_ack",
        )
    _absent(supplied, diagnostics.text, [vars(r) for r in diagnostics.records])
    assert "managed_credits_push_not_acked" in diagnostics.text
    records = [
        r
        for r in diagnostics.records
        if "managed_credits_push_not_acked" in r.getMessage()
    ]
    assert len(records) == 1
    assert records[0].msg["gateway_version"] is None
    assert records[0].msg["reason"] == "gateway_push_not_acknowledged"
