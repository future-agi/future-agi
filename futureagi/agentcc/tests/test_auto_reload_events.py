"""Authoritative-event hooks: replay, lock lifetime, isolation and rollback."""

from uuid import uuid4

import pytest
from django.db import transaction

from agentcc.models.auto_reload_intent import (
    AgentccAutoReloadCreditEvent,
    AgentccAutoReloadIntent,
)
from agentcc.services.auto_reload_events import (
    record_locked_auto_reload_credit,
    record_locked_auto_reload_outcome,
)
from agentcc.tests import test_auto_reload_arming as arming
from agentcc.tests import test_auto_reload_receipt_epoch as receipt_epoch
from agentcc.tests import test_auto_reload_trigger as trigger
from agentcc.tests.auto_reload_event_helpers import complete, credit

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
admin_client = trigger.admin_client
make_client = trigger.make_client
ledger = trigger.ledger
ports = trigger.ports


def locked_account(ports):
    return type(ports.account).objects.select_for_update().get(pk=ports.account.pk)


def test_credit_replay_after_threshold_edit_cannot_advance(
    admin_client, ports, ledger, organization
):
    from agentcc.tests.test_auto_reload_intent_exclusion import put_threshold

    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    # 0 -> 9 is NOT a crossing of the threshold 10 in force at this credit.
    entry, before, after, observed = credit(ports, ledger, organization, 9_000_000)
    assert observed.epoch == 0
    put_threshold(admin_client, "8")
    with transaction.atomic():
        observed = record_locked_auto_reload_credit(
            locked_account(ports),
            ledger_entry_id=entry,
            before_micros=before,
            after_micros=after,
        )
        assert observed.epoch == 0
    assert AgentccAutoReloadCreditEvent.objects.get().threshold_micros == 10_000_000
    receipt_epoch.assert_duplicate(admin_client)
    assert len(ports.checkout.rows()) == 1


def test_crossing_credit_replay_is_exactly_once(
    admin_client, ports, ledger, organization
):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    entry, before, after, observed = credit(ports, ledger, organization, 11_000_000)
    assert observed.epoch == 1
    with transaction.atomic():
        account = locked_account(ports)
        for _ in range(2):
            observed = record_locked_auto_reload_credit(
                account, ledger_entry_id=entry, before_micros=before, after_micros=after
            )
            assert observed.epoch == 1
    assert AgentccAutoReloadCreditEvent.objects.count() == 1
    receipt_epoch.assert_duplicate(admin_client)


def test_credit_uses_threshold_in_force_at_actual_credit(
    admin_client, ports, ledger, organization
):
    from agentcc.tests.test_auto_reload_intent_exclusion import put_threshold

    arming.balance(ledger, organization, 7_000_000)
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    put_threshold(admin_client, "8")
    assert arming.state(ports).epoch == 0  # the edit alone is never an event
    _, _, _, observed = credit(ports, ledger, organization, 9_000_000)
    assert observed.epoch == 1  # real 7 -> 9 credit crosses threshold 8
    assert AgentccAutoReloadCreditEvent.objects.get().threshold_micros == 8_000_000
    receipt_epoch.assert_duplicate(admin_client)
    assert len(ports.checkout.rows()) == 1  # active exclusion still wins


def test_credit_replay_with_changed_balances_refuses(
    admin_client, ports, ledger, organization
):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    entry, before, after, _ = credit(ports, ledger, organization, 11_000_000)
    with pytest.raises(ValueError, match="replay differs"), transaction.atomic():
        record_locked_auto_reload_credit(
            locked_account(ports),
            ledger_entry_id=entry,
            before_micros=before,
            after_micros=after + 1,
        )
    assert arming.state(ports).epoch == 1
    assert AgentccAutoReloadCreditEvent.objects.count() == 1


def test_credit_event_and_rearm_roll_back_with_actual_ledger_credit(
    admin_client, ports, ledger, organization
):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    before = ledger.ledger_balance(organization).balance_micros
    with pytest.raises(RuntimeError, match="synthetic abort"), transaction.atomic():
        credit(ports, ledger, organization, 11_000_000)
        assert arming.state(ports).epoch == 1
        raise RuntimeError("synthetic abort")
    assert ledger.ledger_balance(organization).balance_micros == before
    assert arming.state(ports).epoch == 0
    assert not AgentccAutoReloadCreditEvent.objects.exists()
    receipt_epoch.assert_duplicate(admin_client)


def test_terminal_replay_does_not_rearm_or_clear_a_newer_intent(admin_client, ports):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    old_id = ports.checkout.rows()[0]["intent_id"]
    complete(ports, "failed", intent_id=old_id)
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    new_id = arming.state(ports).intent_id
    complete(ports, "failed", intent_id=old_id)
    assert arming.state(ports).epoch == 1
    assert arming.state(ports).intent_id == new_id
    receipt_epoch.assert_duplicate(admin_client)
    assert len(ports.checkout.rows()) == 2


@pytest.mark.parametrize("outcome", ["credited", "pending_reconcile"])
def test_conflicting_terminal_outcome_is_not_another_event(
    admin_client, ports, outcome
):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    old_id = ports.checkout.rows()[0]["intent_id"]
    complete(ports, "failed", intent_id=old_id)
    with pytest.raises(ValueError, match="terminal outcome cannot be changed"):
        complete(ports, outcome, intent_id=old_id)
    assert AgentccAutoReloadIntent.objects.get().state == "failed"
    assert arming.state(ports).epoch == 1


def test_terminal_state_and_latch_rollback_together(admin_client, ports):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    original = ports.checkout.rows()
    with pytest.raises(RuntimeError, match="synthetic abort"), transaction.atomic():
        complete(ports, "failed", intent_id=original[0]["intent_id"])
        assert arming.state(ports).epoch == 1
        raise RuntimeError("synthetic abort")
    assert AgentccAutoReloadIntent.objects.get().state == "queued"
    assert arming.state(ports).epoch == 0
    assert arming.state(ports).intent_id == original[0]["intent_id"]
    receipt_epoch.assert_duplicate(admin_client)
    assert ports.checkout.rows() == original


def test_foreign_intent_outcome_is_not_accepted(admin_client, ports, organization):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    foreign = type(organization).objects.create(name="Synthetic foreign outcome org")
    foreign_id = f"{foreign.pk}:0"
    AgentccAutoReloadIntent.objects.create(
        organization=foreign, intent_id=foreign_id, epoch=0
    )
    with pytest.raises(AgentccAutoReloadIntent.DoesNotExist):
        complete(ports, "failed", intent_id=foreign_id)
    assert arming.state(ports).epoch == 0
    assert AgentccAutoReloadIntent.objects.get(pk=foreign_id).state == "queued"


@pytest.mark.parametrize("outcome", ["queued", "paid", "unknown", "", True])
def test_non_authoritative_outcome_is_rejected(admin_client, ports, outcome):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    with pytest.raises(ValueError):
        complete(ports, outcome)
    assert AgentccAutoReloadIntent.objects.get().state == "queued"
    assert arming.state(ports).epoch == 0


@pytest.mark.parametrize("before,after", [(0, True), (0.0, 11_000_000), (1, 1), (2, 1)])
def test_credit_hook_rejects_non_credit_or_noninteger_inputs(ports, before, after):
    with pytest.raises(ValueError), transaction.atomic():
        record_locked_auto_reload_credit(
            locked_account(ports),
            ledger_entry_id=str(uuid4()),
            before_micros=before,
            after_micros=after,
        )
    assert not AgentccAutoReloadCreditEvent.objects.exists()


def test_event_hooks_require_account_transaction(ports):
    with pytest.raises(RuntimeError, match="locked account transaction"):
        record_locked_auto_reload_outcome(
            ports.account, intent_id="x", outcome="failed"
        )
    with pytest.raises(RuntimeError, match="locked account transaction"):
        record_locked_auto_reload_credit(
            ports.account, ledger_entry_id="x", before_micros=0, after_micros=11_000_000
        )
