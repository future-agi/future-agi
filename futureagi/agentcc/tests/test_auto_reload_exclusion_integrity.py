"""Reservation integrity across current authorization and adapter rollback."""

from unittest.mock import patch

import pytest
from django.db import transaction

from agentcc.models.auto_reload_intent import AgentccAutoReloadIntent
from agentcc.tests import test_auto_reload_arming as arming
from agentcc.tests import test_auto_reload_receipt_epoch as receipt_epoch
from agentcc.tests import test_auto_reload_trigger as trigger
from agentcc.tests.auto_reload_checkout_double import CheckoutTriggerResult

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
admin_client = trigger.admin_client
make_client = trigger.make_client
ledger = trigger.ledger
ports = trigger.ports


def test_saved_method_change_and_reack_do_not_replace_inflight(admin_client, ports):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    original = ports.checkout.rows()
    new_method = "pm_synthetic_second_owned"
    ports.checkout.owned_methods[new_method] = ports.account.organization_id
    # Model the saved-method writer taking the required account lock. Receipt
    # acknowledgement then goes through the real settings PUT, not a fake guard.
    with transaction.atomic():
        account = (
            type(ports.account).objects.select_for_update().get(pk=ports.account.pk)
        )
        account.autoreload_payment_method_id = new_method
        account.save(update_fields=["autoreload_payment_method_id"])
    refused = trigger.post(admin_client)
    assert refused.status_code == 409
    assert refused.json()["code"] == "auto_reload_consent_required"
    receipt_epoch.put_enabled(admin_client)
    receipt_epoch.assert_duplicate(admin_client)
    assert ports.checkout.calls[-1]["payment_method_id"] == new_method
    assert ports.checkout.rows() == original
    assert arming.state(ports).epoch == 0
    assert AgentccAutoReloadIntent.objects.get().intent_id == original[0]["intent_id"]


def test_initial_revocation_creates_no_intent_or_outbox(admin_client, ports):
    type(ports.account).objects.filter(pk=ports.account.pk).update(
        autoreload_enabled=False
    )
    response = trigger.post(admin_client)
    assert response.status_code == 409
    ports.load_checkout.assert_not_called()
    assert not AgentccAutoReloadIntent.objects.exists()
    assert not ports.checkout.rows()


def test_reservation_rolls_back_with_failing_adapter_insert(admin_client, ports):
    def fail_after_insert():
        assert AgentccAutoReloadIntent.objects.get().state == "queued"
        raise RuntimeError("synthetic rollback")

    ports.checkout.after_insert = fail_after_insert
    assert trigger.post(admin_client).status_code == 503
    assert not AgentccAutoReloadIntent.objects.exists()
    assert not ports.checkout.rows()
    ports.checkout.after_insert = None
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    assert AgentccAutoReloadIntent.objects.count() == 1
    assert len(ports.checkout.rows()) == 1


@pytest.mark.parametrize(
    "reason", ["disabled", "consent_stale", "payment_method_mismatch"]
)
def test_enqueue_refusal_is_not_a_terminal_event(admin_client, ports, reason):
    with patch.object(
        ports.checkout,
        "request_auto_reload_checkout",
        return_value=CheckoutTriggerResult("refused", reason),
    ):
        assert trigger.post(admin_client).status_code == 409
    assert not AgentccAutoReloadIntent.objects.exists()
    assert not ports.checkout.rows()
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    assert arming.state(ports).epoch == 0
    assert AgentccAutoReloadIntent.objects.get().intent_id.endswith(":0")
