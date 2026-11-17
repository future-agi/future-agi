"""Cross-org and missing-account refusal around the frozen checkout boundary."""

from inspect import Parameter, signature
from uuid import uuid4

import pytest

from agentcc.models.auto_reload_consent import AgentccAutoReloadConsent
from agentcc.tests import test_auto_reload_trigger as trigger

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
admin_client = trigger.admin_client
make_client = trigger.make_client
ledger = trigger.ledger
ports = trigger.ports


def test_missing_account_never_creates_account_or_outbox(admin_client, ports):
    account_type = type(ports.account)
    account_type.objects.filter(pk=ports.account.pk).delete()
    response = trigger.post(admin_client)
    assert response.status_code == 409
    assert not account_type.objects.exists()
    ports.load_checkout.assert_not_called()
    assert not ports.checkout.rows()


def test_receipt_cannot_be_borrowed_from_another_org(admin_client, ports, organization):
    org_type = type(organization)
    foreign = org_type.objects.create(name="Synthetic foreign consent org")
    AgentccAutoReloadConsent.objects.filter(pk=ports.receipt.pk).update(
        organization_id=foreign.pk
    )
    response = trigger.post(admin_client)
    assert response.status_code == 409
    ports.load_checkout.assert_not_called()
    assert not ports.checkout.rows()


def test_double_has_exact_keyword_only_frozen_boundary_signature(ports):
    parameters = signature(ports.checkout.request_auto_reload_checkout).parameters
    assert list(parameters) == [
        "org_id",
        "consent_receipt_id",
        "intent_id",
        "amount_micros",
        "payment_method_id",
        "consent_revision",
    ]
    assert all(p.kind == Parameter.KEYWORD_ONLY for p in parameters.values())


def test_distinct_caller_uuids_for_unchanged_event_queue_once(admin_client, ports):
    first = trigger.post(admin_client)
    second = trigger.post(admin_client, str(uuid4()))
    assert first.status_code == second.status_code == 200
    assert first.json()["result"] == {"status": "queued"}
    assert second.json()["result"] == {"status": "duplicate"}
    assert len(ports.checkout.rows()) == 1
    assert ports.checkout.calls[0]["intent_id"] == ports.checkout.calls[1]["intent_id"]
