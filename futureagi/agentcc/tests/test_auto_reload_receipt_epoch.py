"""Receipt churn must not create another checkout within one arming epoch."""

from uuid import uuid4

import pytest
from django.db import transaction

from agentcc.services.auto_reload_consent import get_current_auto_reload_consent
from agentcc.tests import test_auto_reload_arming as arming
from agentcc.tests import test_auto_reload_trigger as trigger
from agentcc.tests.auto_reload_event_helpers import complete, credit

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
admin_client = trigger.admin_client
make_client = trigger.make_client
ledger = trigger.ledger
ports = trigger.ports


def put_enabled(client, *, amount="25"):
    """Use the real settings route, lock and receipt writer, not a receipt mock."""
    response = client.put(
        trigger.wallet.BASE + "settings/",
        {
            "low_balance_threshold_usd": None,
            "auto_reload": {
                "enabled": True,
                "consent_version": trigger.binding.VERSION,
                "threshold_usd": "10",
                "amount_usd": amount,
            },
        },
        format="json",
    )
    assert response.status_code == 200, response.content


def current_receipt(ports):
    ports.account.refresh_from_db()
    receipt = get_current_auto_reload_consent(ports.account)
    assert receipt is not None
    return receipt


def assert_duplicate(client):
    response = trigger.post(client, str(uuid4()))
    assert response.status_code == 200, response.content
    assert response.json()["result"] == {"status": "duplicate"}


def test_unchanged_settings_put_keeps_one_intent_in_epoch(admin_client, ports):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    before = ports.checkout.rows()
    assert len(before) == 1
    first_id = before[0]["intent_id"]
    for _ in range(2):
        previous = current_receipt(ports)
        put_enabled(admin_client)
        receipt = current_receipt(ports)
        assert receipt.pk != previous.pk
        assert receipt.amount_micros == previous.amount_micros
        assert receipt.threshold_micros == previous.threshold_micros
        assert receipt.payment_method_id == previous.payment_method_id
        assert receipt.consent_version == previous.consent_version
        # This assertion must fail on r3 before accessing the new latch field.
        assert_duplicate(admin_client)
        assert ports.checkout.calls[-1]["consent_receipt_id"] == receipt.pk
        assert ports.checkout.calls[-1]["consent_revision"] == 1
        assert ports.checkout.calls[-1]["intent_id"] == first_id
        assert ports.checkout.rows() == before
        assert arming.state(ports).epoch == 0
        assert arming.state(ports).intent_id == first_id
    assert first_id == f"{ports.account.organization_id}:0"
    assert len(ports.checkout.calls) == 3


def test_changed_amount_waits_for_next_epoch(admin_client, ports, ledger, organization):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    before = ports.checkout.rows()
    assert len(before) == 1
    first_id = before[0]["intent_id"]
    assert before[0]["amount_micros"] == 25_000_000
    put_enabled(admin_client, amount="50")
    receipt = current_receipt(ports)
    assert receipt.pk != ports.receipt.pk
    assert receipt.amount_micros == 50_000_000
    assert_duplicate(admin_client)
    # Authorization follows current consent; idempotency preserves old payload.
    assert ports.checkout.calls[-1]["consent_receipt_id"] == receipt.pk
    assert ports.checkout.calls[-1]["amount_micros"] == 50_000_000
    assert ports.checkout.calls[-1]["intent_id"] == first_id
    assert ports.checkout.rows() == before
    assert arming.state(ports).epoch == 0
    assert arming.state(ports).intent_id == first_id

    credit(ports, ledger, organization, 11_000_000)
    complete(ports, "credited", intent_id=first_id)
    above = trigger.post(admin_client)
    assert above.status_code == 409, above.content
    assert above.json()["code"] == "auto_reload_threshold_not_reached"
    assert arming.state(ports).epoch == 1
    assert arming.state(ports).intent_id == ""
    arming.balance(ledger, organization, 9_000_000)
    response = trigger.post(admin_client)
    assert response.status_code == 200, response.content
    assert response.json()["result"] == {"status": "queued"}
    assert_duplicate(admin_client)
    rows = {row["intent_id"]: row for row in ports.checkout.rows()}
    second_id = f"{organization.pk}:1"
    assert set(rows) == {first_id, second_id}
    assert rows[first_id] == before[0]
    assert rows[second_id]["amount_micros"] == 50_000_000
    assert rows[second_id]["consent_receipt_id"] == str(receipt.pk)
    assert arming.state(ports).intent_id == second_id
    assert arming.state(ports).epoch == 1


def test_revoke_then_reconsent_keeps_one_intent_in_epoch(admin_client, ports):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    before = ports.checkout.rows()
    assert len(before) == 1
    first_id = before[0]["intent_id"]
    response = admin_client.put(
        trigger.wallet.BASE + "settings/",
        {"low_balance_threshold_usd": None, "auto_reload": {"enabled": False}},
        format="json",
    )
    assert response.status_code == 200, response.content
    ports.account.refresh_from_db()
    assert not ports.account.autoreload_enabled
    ports.load_checkout.reset_mock()
    refused = trigger.post(admin_client, str(uuid4()))
    assert refused.status_code == 409, refused.content
    assert refused.json()["code"] == "auto_reload_consent_required"
    ports.load_checkout.assert_not_called()
    assert len(ports.checkout.calls) == 1
    assert ports.checkout.rows() == before
    assert arming.state(ports).epoch == 0

    put_enabled(admin_client)
    receipt = current_receipt(ports)
    assert receipt.pk != ports.receipt.pk
    assert_duplicate(admin_client)
    assert ports.checkout.calls[-1]["consent_receipt_id"] == receipt.pk
    assert ports.checkout.calls[-1]["intent_id"] == first_id
    assert ports.checkout.rows() == before
    assert arming.state(ports).epoch == 0
    assert arming.state(ports).intent_id == first_id
    assert len(ports.checkout.calls) == 2


def test_first_intent_latch_rolls_back_on_existing_arming_row(admin_client, ports):
    from agentcc.services.auto_reload_arming import observe_locked_auto_reload_balance

    with transaction.atomic():
        account = (
            type(ports.account).objects.select_for_update().get(pk=ports.account.pk)
        )
        observe_locked_auto_reload_balance(account, balance_micros=0)
    assert arming.state(ports).intent_id == ""

    def fail_after_insert():
        # The latch has already been persisted inside the locked transaction.
        assert arming.state(ports).intent_id == f"{ports.account.organization_id}:0"
        raise RuntimeError("synthetic latch rollback")

    ports.checkout.after_insert = fail_after_insert
    assert trigger.post(admin_client).status_code == 503
    assert not ports.checkout.rows()
    assert arming.state(ports).intent_id == ""
    assert arming.state(ports).epoch == 0
    ports.checkout.after_insert = None
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    assert len(ports.checkout.rows()) == 1
    assert arming.state(ports).intent_id == ports.checkout.rows()[0]["intent_id"]
