"""Committed top-ups remain pending after an ordinary gateway push failure."""

import logging
import secrets
import traceback
from unittest.mock import patch

import httpx
import pytest

from agentcc.tests import test_managed_credits_api as wallet

pytestmark = pytest.mark.integration
ledger = wallet.ledger


@pytest.mark.parametrize("failure", [RuntimeError, httpx.InvalidURL])
def test_topup_remains_committed_and_value_free_after_gateway_failure(
    ledger, organization, caplog, django_capture_on_commit_callbacks, failure
):
    from ee.usage.services import gateway_push

    supplied = "sk_test_" + secrets.token_urlsafe(32)
    caplog.set_level(logging.WARNING)
    # The ledger's push is the coalesced gateway push module's, re-exported.
    assert ledger.push_granted is gateway_push.push_granted
    # A Checkout session credits only with the PaymentIntent that paid it: one
    # without it is refused before anything is written.
    with pytest.raises(ledger.ManagedCreditError):
        ledger.apply_topup(organization, 1_000_000, stripe_session_id="cs_synthetic")
    assert ledger.ledger_entries(organization).count() == 0
    escaped = False
    trace_observed = False
    try:
        with (
            patch.object(
                gateway_push.gateway_credits,
                "put_granted",
                side_effect=failure(supplied),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            ledger.apply_topup(
                organization,
                1_000_000,
                stripe_session_id="cs_synthetic",
                payment_intent_id="pi_synthetic",
            )
    except Exception as exc:
        escaped = True
        trace_observed = supplied in "".join(traceback.format_exception(exc))
    assert trace_observed is False, "supplied value reached an observable surface"
    assert escaped is False, "post-commit dependency exception escaped the boundary"
    observed = any(
        supplied in value
        for record in caplog.records
        for value in (
            record.getMessage(),
            str(vars(record)),
            logging.Formatter().format(record),
        )
    )
    assert observed is False, "supplied value reached an observable surface"
    account = ledger.get_account(organization)
    assert account.granted_total_micros == 1_000_000
    assert account.version == 1
    assert account.gateway_acked_version == 0
    assert ledger.ledger_entries(organization).count() == 1
    records = [
        record
        for record in caplog.records
        if "managed_credits_push_failed" in record.getMessage()
    ]
    assert len(records) == 1
    assert records[0].msg["exception_type"] == failure.__name__
    assert records[0].msg["reason"] == "gateway_push_failed"
    assert records[0].exc_info is None
