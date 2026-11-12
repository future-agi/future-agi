"""One nonterminal intent even across arbitrary mutable-settings churn."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from agentcc.tests import test_auto_reload_arming as arming
from agentcc.tests import test_auto_reload_receipt_epoch as receipt_epoch
from agentcc.tests import test_auto_reload_trigger as trigger
from agentcc.tests.auto_reload_event_helpers import complete, credit

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
admin_client = trigger.admin_client
make_client = trigger.make_client
ledger = trigger.ledger
ports = trigger.ports


def intents(ports):
    # Lazy import: the threshold sequence must reach a behavioral RED on r4.
    from agentcc.models.auto_reload_intent import AgentccAutoReloadIntent

    return AgentccAutoReloadIntent.objects.filter(
        organization_id=ports.account.organization_id
    )


def put_threshold(client, threshold):
    response = client.put(
        trigger.wallet.BASE + "settings/",
        {
            "low_balance_threshold_usd": None,
            "auto_reload": {
                "enabled": True,
                "consent_version": trigger.binding.VERSION,
                "threshold_usd": threshold,
                "amount_usd": "25",
            },
        },
        format="json",
    )
    assert response.status_code == 200, response.content


def test_threshold_10_8_10_at_constant_9_keeps_one_nonterminal(
    admin_client, ports, ledger, organization
):
    arming.balance(ledger, organization, 9_000_000)
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    original = ports.checkout.rows()
    first_receipt = receipt_epoch.current_receipt(ports)
    put_threshold(admin_client, "8")
    lower_receipt = receipt_epoch.current_receipt(ports)
    assert lower_receipt.pk != first_receipt.pk
    lower = trigger.post(admin_client, str(uuid4()))
    # Complete the sequence before asserting so r4 fails on its SECOND enqueue,
    # not on a new model import, fixture, missing column or threshold response.
    put_threshold(admin_client, "10")
    restored_receipt = receipt_epoch.current_receipt(ports)
    assert restored_receipt.pk != lower_receipt.pk
    restored = trigger.post(admin_client, str(uuid4()))
    assert restored.status_code == 200, restored.content
    assert restored.json()["result"] == {"status": "duplicate"}
    assert lower.status_code == 200, lower.content
    assert lower.json()["result"] == {"status": "duplicate"}
    assert ports.checkout.rows() == original
    assert arming.state(ports).epoch == 0
    assert arming.state(ports).intent_id == original[0]["intent_id"]
    assert ledger.ledger_balance(organization).balance_micros == 9_000_000
    assert intents(ports).count() == 1
    assert intents(ports).get().state == "queued"


def test_plain_high_observation_is_not_a_credit_or_terminal_event(
    admin_client, ports, ledger, organization
):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    original = ports.checkout.rows()
    arming.balance(ledger, organization, 11_000_000)
    response = trigger.post(admin_client)
    assert response.json()["result"] == {"status": "duplicate"}
    assert arming.state(ports).epoch == 0
    arming.balance(ledger, organization, 9_000_000)
    receipt_epoch.assert_duplicate(admin_client)
    assert ports.checkout.rows() == original


def test_credit_rearm_cannot_bypass_an_inflight_intent(
    admin_client, ports, ledger, organization
):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    original = ports.checkout.rows()
    _, _, _, observed = credit(ports, ledger, organization, 11_000_000)
    assert observed.epoch == 1
    assert observed.intent_id == original[0]["intent_id"]
    receipt_epoch.assert_duplicate(admin_client)
    arming.balance(ledger, organization, 9_000_000)
    receipt_epoch.assert_duplicate(admin_client)
    assert ports.checkout.rows() == original
    assert intents(ports).count() == 1


def test_terminal_success_then_debit_allows_exactly_one_new_intent(
    admin_client, ports, ledger, organization
):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    first_id = ports.checkout.rows()[0]["intent_id"]
    credit(ports, ledger, organization, 11_000_000)
    complete(ports, "credited", intent_id=first_id)
    assert arming.state(ports).epoch == 1
    assert arming.state(ports).intent_id == ""
    above = trigger.post(admin_client)
    assert above.status_code == 409
    assert above.json()["code"] == "auto_reload_threshold_not_reached"
    arming.balance(ledger, organization, 9_000_000)
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    receipt_epoch.assert_duplicate(admin_client)
    assert {row["intent_id"] for row in ports.checkout.rows()} == {
        first_id,
        f"{organization.pk}:1",
    }
    assert intents(ports).filter(state="queued").count() == 1
    assert intents(ports).get(pk=first_id).state == "credited"


@pytest.mark.parametrize("outcome", ["failed", "refused"])
def test_terminal_failure_or_refusal_allows_retry(admin_client, ports, outcome):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    original = ports.checkout.rows()[0]
    complete(ports, outcome, intent_id=original["intent_id"])
    assert arming.state(ports).epoch == 1
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    receipt_epoch.assert_duplicate(admin_client)
    rows = {row["intent_id"]: row for row in ports.checkout.rows()}
    assert rows[original["intent_id"]] == original
    assert len(rows) == 2
    assert (
        intents(ports).exclude(state__in=("credited", "failed", "refused")).count() == 1
    )


def test_pending_reconcile_never_releases_exclusion(
    admin_client, ports, ledger, organization
):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    original = ports.checkout.rows()
    complete(ports, "pending_reconcile", intent_id=original[0]["intent_id"])
    assert arming.state(ports).epoch == 0
    credit(ports, ledger, organization, 11_000_000)
    arming.balance(ledger, organization, 9_000_000)
    for threshold in ("8", "10"):
        put_threshold(admin_client, threshold)
        receipt_epoch.assert_duplicate(admin_client)
    assert ports.checkout.rows() == original
    assert intents(ports).get().state == "pending_reconcile"


@pytest.mark.parametrize("first_state", ["queued", "pending_reconcile"])
@pytest.mark.parametrize("second_state", ["queued", "pending_reconcile"])
def test_database_alone_rejects_second_nonterminal_insert(
    organization, first_state, second_state
):
    from agentcc.models.auto_reload_intent import AgentccAutoReloadIntent

    assert connection.vendor == "postgresql", (
        "partial unique index requires PostgreSQL proof"
    )
    AgentccAutoReloadIntent.objects.create(
        organization=organization,
        intent_id=f"{organization.pk}:0",
        epoch=0,
        state=first_state,
    )
    # Deliberately bypass service validation and account locking. Distinct PKs
    # ensure the partial organization constraint, not key idempotency, rejects it.
    with pytest.raises(IntegrityError) as error, transaction.atomic():
        AgentccAutoReloadIntent.objects.create(
            organization=organization,
            intent_id=f"{organization.pk}:1",
            epoch=1,
            state=second_state,
        )
    assert (
        error.value.__cause__.diag.constraint_name == "agentcc_reload_one_nonterminal"
    )
    assert (
        AgentccAutoReloadIntent.objects.filter(organization=organization).count() == 1
    )


def test_duplicate_revalidates_current_method_ownership(admin_client, ports):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    original = ports.checkout.rows()
    ports.checkout.owned_methods[trigger.binding.METHOD] = uuid4()
    response = trigger.post(admin_client)
    assert response.status_code == 409
    assert response.json()["code"] == "auto_reload_payment_method_mismatch"
    assert ports.checkout.rows() == original
    assert intents(ports).get().state == "queued"


def test_existing_intent_cannot_reinsert_a_missing_outbox_row(admin_client, ports):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM test_gateway_checkout_outbox")
    response = trigger.post(admin_client)
    assert response.status_code == 503
    assert not ports.checkout.rows()  # unexpected adapter insert was rolled back
    assert intents(ports).count() == 1
