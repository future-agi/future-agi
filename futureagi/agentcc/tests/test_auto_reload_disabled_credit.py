"""Disabled-credit hook regressions; not proof of the protected claim consumer.

The fixture snapshots an authorized queued payload before disabling consent,
representing a claim that has already succeeded. Payment success is a trusted
input here; only the real synthetic ledger and gateway hooks execute.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import transaction

from agentcc.models.auto_reload_intent import (
    AgentccAutoReloadCreditEvent,
    AgentccAutoReloadIntent,
)
from agentcc.services.auto_reload_consent import has_current_auto_reload_consent
from agentcc.services.auto_reload_events import (
    record_locked_auto_reload_credit,
    record_locked_auto_reload_outcome,
)
from agentcc.tests import test_auto_reload_arming as arming
from agentcc.tests import test_auto_reload_receipt_epoch as receipt_epoch
from agentcc.tests import test_auto_reload_trigger as trigger
from agentcc.tests.auto_reload_event_helpers import credit
from agentcc.tests.test_auto_reload_events import locked_account

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
admin_client = trigger.admin_client
make_client = trigger.make_client
ledger = trigger.ledger
ports = trigger.ports


def disable(client, ports):
    response = client.put(
        trigger.wallet.BASE + "settings/",
        {"low_balance_threshold_usd": None, "auto_reload": {"enabled": False}},
        format="json",
    )
    assert response.status_code == 200, response.content
    ports.account.refresh_from_db()
    assert not ports.account.autoreload_enabled
    assert ports.account.autoreload_threshold_micros is None
    assert not has_current_auto_reload_consent(ports.account)


@pytest.fixture
def claimed_before_disable(admin_client, ports, ledger, organization):
    arming.balance(ledger, organization, 9_000_000)
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    with transaction.atomic():
        assert has_current_auto_reload_consent(locked_account(ports))
        # Immutable pre-disable payload, not a real protected consumer claim.
        claimed = ports.checkout.rows()[0]
    assert claimed["amount_micros"] == 25_000_000
    disable(admin_client, ports)
    assert arming.state(ports).epoch == 0
    assert arming.state(ports).intent_id == claimed["intent_id"]
    refused = trigger.post(admin_client)
    assert refused.status_code == 409, refused.content
    assert refused.json()["code"] == "auto_reload_consent_required"
    assert len(ports.checkout.calls) == 1
    return claimed


def deliver_success(ports, ledger, organization, claimed, entry_id, *, terminal=False):
    """Replay the same credit and original balances, never recompute its amount."""
    with transaction.atomic():
        account = locked_account(ports)
        ledger.adjust(
            organization,
            claimed["amount_micros"],
            idempotency_key=entry_id,
            actor=None,
            reason="synthetic already-claimed successful top-up",
        )
        observed = record_locked_auto_reload_credit(
            account,
            ledger_entry_id=entry_id,
            before_micros=9_000_000,
            after_micros=34_000_000,
        )
        if terminal:
            record_locked_auto_reload_outcome(
                account, intent_id=claimed["intent_id"], outcome="credited"
            )
        return observed


def assert_one_disabled_receipt(ports, entry_id):
    event = AgentccAutoReloadCreditEvent.objects.get()
    assert event.organization_id == ports.account.organization_id
    assert event.ledger_entry_id == entry_id
    assert event.before_micros == 9_000_000
    assert event.after_micros == 34_000_000
    assert event.threshold_micros is None
    return event


def test_disable_after_claim_successful_credit_once_without_renewed_consent(
    admin_client, ports, ledger, organization, claimed_before_disable
):
    claimed = claimed_before_disable
    entry_id = f"disabled-credit:{uuid4()}"
    observed = deliver_success(
        ports, ledger, organization, claimed, entry_id, terminal=True
    )
    # The credit hook itself preserves epoch/latch; only the terminal hook releases.
    assert observed.epoch == 0
    assert observed.intent_id == claimed["intent_id"]
    assert_one_disabled_receipt(ports, entry_id)
    assert ledger.ledger_balance(organization).balance_micros == 34_000_000
    assert AgentccAutoReloadIntent.objects.get().state == "credited"
    assert arming.state(ports).epoch == 1
    assert arming.state(ports).intent_id == ""
    for _ in range(2):
        deliver_success(ports, ledger, organization, claimed, entry_id, terminal=True)
    assert ledger.ledger_balance(organization).balance_micros == 34_000_000
    assert_one_disabled_receipt(ports, entry_id)
    assert arming.state(ports).epoch == 1
    assert AgentccAutoReloadIntent.objects.count() == 1
    assert ports.checkout.rows() == [claimed]
    ports.account.refresh_from_db()
    assert not has_current_auto_reload_consent(ports.account)
    receipt_epoch.put_enabled(admin_client)
    above = trigger.post(admin_client)
    assert above.status_code == 409, above.content
    assert above.json()["code"] == "auto_reload_threshold_not_reached"
    assert arming.state(ports).epoch == 1
    assert AgentccAutoReloadIntent.objects.count() == 1
    assert ports.checkout.rows() == [claimed]


def test_same_credit_replay_while_disabled_preserves_active_intent_and_epoch(
    ports, ledger, organization, claimed_before_disable
):
    claimed = claimed_before_disable
    entry_id = f"disabled-replay:{uuid4()}"
    before_state = arming.state(ports)
    for _ in range(3):
        observed = deliver_success(ports, ledger, organization, claimed, entry_id)
        assert observed.epoch == before_state.epoch
        assert observed.intent_id == before_state.intent_id
        assert observed.below_threshold == before_state.below_threshold
    assert_one_disabled_receipt(ports, entry_id)
    assert ledger.ledger_balance(organization).balance_micros == 34_000_000
    assert AgentccAutoReloadIntent.objects.get().state == "queued"
    assert ports.checkout.rows() == [claimed]


def test_reack_and_credit_replay_cannot_create_second_intent_in_episode(
    admin_client, ports, ledger, organization, claimed_before_disable
):
    claimed = claimed_before_disable
    entry_id = f"disabled-reack:{uuid4()}"
    deliver_success(ports, ledger, organization, claimed, entry_id)
    receipt_epoch.put_enabled(admin_client)
    # The original 9 -> 34 would cross the NEW threshold 10, but is already receipted.
    deliver_success(ports, ledger, organization, claimed, entry_id)
    receipt_epoch.assert_duplicate(admin_client)
    assert_one_disabled_receipt(ports, entry_id)
    assert ledger.ledger_balance(organization).balance_micros == 34_000_000
    assert arming.state(ports).epoch == 0
    assert arming.state(ports).intent_id == claimed["intent_id"]
    assert AgentccAutoReloadIntent.objects.get().state == "queued"
    assert ports.checkout.rows() == [claimed]


def test_configured_credit_can_replay_after_disable(
    admin_client, ports, ledger, organization
):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    entry_id, before, after, _ = credit(ports, ledger, organization, 11_000_000)
    original = arming.state(ports)
    disable(admin_client, ports)
    with transaction.atomic():
        observed = record_locked_auto_reload_credit(
            locked_account(ports),
            ledger_entry_id=entry_id,
            before_micros=before,
            after_micros=after,
        )
    assert observed.epoch == original.epoch == 1
    assert observed.intent_id == original.intent_id
    assert AgentccAutoReloadCreditEvent.objects.get().threshold_micros == 10_000_000
    assert len(ports.checkout.rows()) == 1


def test_unconfigured_credit_is_receipted_without_minting_an_intent(
    admin_client, ports, ledger, organization
):
    disable(admin_client, ports)
    _, _, _, observed = credit(ports, ledger, organization, 1_000_000)
    assert observed.epoch == 0
    assert observed.intent_id == ""
    assert AgentccAutoReloadCreditEvent.objects.get().threshold_micros is None
    assert not AgentccAutoReloadIntent.objects.exists()
    assert not ports.checkout.rows()


def test_disabled_credit_replay_with_conflicting_balances_refuses(
    ports, ledger, organization, claimed_before_disable
):
    entry_id = f"disabled-conflict:{uuid4()}"
    deliver_success(ports, ledger, organization, claimed_before_disable, entry_id)
    with pytest.raises(ValueError, match="replay differs"), transaction.atomic():
        record_locked_auto_reload_credit(
            locked_account(ports),
            ledger_entry_id=entry_id,
            before_micros=9_000_000,
            after_micros=34_000_001,
        )
    assert_one_disabled_receipt(ports, entry_id)
    assert ledger.ledger_balance(organization).balance_micros == 34_000_000
    assert arming.state(ports).epoch == 0


def test_disabled_credit_receipt_and_outcome_roll_back_with_ledger(
    ports, ledger, organization, claimed_before_disable
):
    claimed = claimed_before_disable
    entry_id = f"disabled-rollback:{uuid4()}"
    with pytest.raises(RuntimeError, match="synthetic abort"), transaction.atomic():
        deliver_success(ports, ledger, organization, claimed, entry_id, terminal=True)
        raise RuntimeError("synthetic abort")
    assert ledger.ledger_balance(organization).balance_micros == 9_000_000
    assert not AgentccAutoReloadCreditEvent.objects.exists()
    assert AgentccAutoReloadIntent.objects.get().state == "queued"
    assert arming.state(ports).epoch == 0
    assert arming.state(ports).intent_id == claimed["intent_id"]
    deliver_success(ports, ledger, organization, claimed, entry_id, terminal=True)
    assert ledger.ledger_balance(organization).balance_micros == 34_000_000
    assert_one_disabled_receipt(ports, entry_id)


@pytest.mark.parametrize("invalid", [True, False, 1.0, "1", None, Decimal("1")])
@pytest.mark.parametrize("field", ["before_micros", "after_micros"])
def test_disabled_credit_keeps_strict_integer_balance_checks(
    admin_client, ports, invalid, field
):
    disable(admin_client, ports)
    balances = {"before_micros": 0, "after_micros": 2}
    balances[field] = invalid
    with pytest.raises(ValueError, match="integer micros"), transaction.atomic():
        record_locked_auto_reload_credit(
            locked_account(ports), ledger_entry_id=str(uuid4()), **balances
        )
    assert not AgentccAutoReloadCreditEvent.objects.exists()


@pytest.mark.parametrize("threshold", [True, False, 10.0, "10", Decimal("10")])
def test_nonnull_threshold_still_requires_exact_integer(ports, threshold):
    with pytest.raises(ValueError, match="integer micros"), transaction.atomic():
        account = locked_account(ports)
        account.autoreload_threshold_micros = threshold
        record_locked_auto_reload_credit(
            account, ledger_entry_id=str(uuid4()), before_micros=0, after_micros=20
        )
    assert not AgentccAutoReloadCreditEvent.objects.exists()


@pytest.mark.parametrize("before,after", [(1, 1), (2, 1)])
def test_disabled_hook_still_rejects_nonpositive_credits(
    admin_client, ports, before, after
):
    disable(admin_client, ports)
    with (
        pytest.raises(ValueError, match="positive ledger credit"),
        transaction.atomic(),
    ):
        record_locked_auto_reload_credit(
            locked_account(ports),
            ledger_entry_id=str(uuid4()),
            before_micros=before,
            after_micros=after,
        )
    assert not AgentccAutoReloadCreditEvent.objects.exists()
